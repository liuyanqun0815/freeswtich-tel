import importlib.util
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient

from support import get_test_session, seed_user
from vr_api.main import app
from vr_common.models import AiScene

_MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "20261027_0002_ai_scene_providers.py"


def _admin_headers(client: TestClient) -> dict[str, str]:
    seed_user("admin_ai", "admin-pass", "admin", must_change_password=False)
    login = client.post("/auth/login", json={"username": "admin_ai", "password": "admin-pass"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_ai_scene_crud_with_provider_configs():
    client = TestClient(app)
    headers = _admin_headers(client)
    created = client.post(
        "/ai-scenes",
        headers=headers,
        json={
            "name": "售后",
            "opening": "您好",
            "fallback": "transfer",
            "system_prompt": "你是客服",
            "transfer_skill_group_id": 7,
            "llm": {
                "vendor": "openai",
                "base_url": "https://api.deepseek.com/v1",
                "api_key_env": "DEEPSEEK_API_KEY",
                "model": "deepseek-chat",
            },
        },
    )
    assert created.status_code == 200
    scene = created.json()
    assert scene["llm"]["model"] == "deepseek-chat"
    assert scene["asr"] == {}
    assert scene["transfer_skill_group_id"] == 7
    assert scene["system_prompt"] == "你是客服"

    updated = client.put(
        f"/ai-scenes/{scene['id']}",
        headers=headers,
        json={"name": "售后", "fallback": "hangup", "tts": {"vendor": "openai", "voice": "alloy"}},
    )
    assert updated.status_code == 200
    assert updated.json()["fallback"] == "hangup"
    assert updated.json()["tts"]["voice"] == "alloy"
    assert updated.json()["llm"] == {"api_key_set": False}

    listed = client.get("/ai-scenes", headers=headers).json()["items"]
    assert [item["name"] for item in listed] == ["售后"]

    assert client.delete(f"/ai-scenes/{scene['id']}", headers=headers).status_code == 204
    assert client.delete(f"/ai-scenes/{scene['id']}", headers=headers).status_code == 404


def test_ai_scene_rejects_plain_api_key_and_bad_fallback():
    client = TestClient(app)
    headers = _admin_headers(client)
    for config in ("asr", "tts"):
        with_key = client.post(
            "/ai-scenes", headers=headers, json={"name": "x", "fallback": "hangup", config: {"api_key": "sk-123"}}
        )
        assert with_key.status_code == 400
    bad_fallback = client.post("/ai-scenes", headers=headers, json={"name": "x", "fallback": "retry"})
    assert bad_fallback.status_code == 400
    missing_group = client.post("/ai-scenes", headers=headers, json={"name": "x", "fallback": "transfer"})
    assert missing_group.status_code == 400
    assert "技能组" in missing_group.json()["detail"]


def test_ai_scene_rejects_api_key_env_not_ending_with_api_key():
    client = TestClient(app)
    headers = _admin_headers(client)
    for config in ("asr", "tts", "llm"):
        resp = client.post(
            "/ai-scenes",
            headers=headers,
            json={"name": "x", "fallback": "hangup", config: {"api_key_env": "JWT_SECRET"}},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "api_key_env 必须是大写字母开头、以 _API_KEY 结尾的变量名"
    lower = client.post(
        "/ai-scenes",
        headers=headers,
        json={"name": "x", "fallback": "hangup", "llm": {"api_key_env": "deepseek_API_KEY"}},
    )
    assert lower.status_code == 400


def _stored_llm(scene_id: int) -> dict:
    db = next(get_test_session())
    try:
        return json.loads(db.get(AiScene, scene_id).llm_json)
    finally:
        db.close()


def test_llm_api_key_is_write_only_and_kept_until_base_url_changes():
    client = TestClient(app)
    headers = _admin_headers(client)
    llm = {"vendor": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"}
    created = client.post(
        "/ai-scenes", headers=headers, json={"name": "x", "fallback": "hangup", "llm": {**llm, "api_key": "sk-secret"}}
    )
    assert created.status_code == 200
    scene = created.json()
    assert "api_key" not in scene["llm"]
    assert scene["llm"]["api_key_set"] is True
    assert _stored_llm(scene["id"])["api_key"] == "sk-secret"
    assert "sk-secret" not in client.get("/ai-scenes", headers=headers).text

    kept = client.put(
        f"/ai-scenes/{scene['id']}",
        headers=headers,
        json={"name": "x", "fallback": "hangup", "llm": {**llm, "api_key": ""}},
    )
    assert kept.json()["llm"]["api_key_set"] is True
    assert _stored_llm(scene["id"])["api_key"] == "sk-secret"

    replaced = client.put(
        f"/ai-scenes/{scene['id']}",
        headers=headers,
        json={"name": "x", "fallback": "hangup", "llm": {**llm, "api_key": "sk-new"}},
    )
    assert replaced.json()["llm"]["api_key_set"] is True
    assert _stored_llm(scene["id"])["api_key"] == "sk-new"

    moved = client.put(
        f"/ai-scenes/{scene['id']}",
        headers=headers,
        json={"name": "x", "fallback": "hangup", "llm": {**llm, "base_url": "https://other.example/v1"}},
    )
    assert moved.json()["llm"]["api_key_set"] is False
    assert "api_key" not in _stored_llm(scene["id"])


def test_ai_scene_tencent_asr_keeps_credentials_out_of_the_scene():
    client = TestClient(app)
    headers = _admin_headers(client)
    tencent = {"vendor": "tencent", "engine_model_type": "8k_zh"}
    ok = client.post("/ai-scenes", headers=headers, json={"name": "x", "fallback": "hangup", "asr": tencent})
    assert ok.status_code == 200
    assert ok.json()["asr"] == tencent

    for field in ("secret_id", "secret_key"):
        plain = client.post(
            "/ai-scenes",
            headers=headers,
            json={"name": "x", "fallback": "hangup", "asr": {**tencent, field: "abc"}},
        )
        assert plain.status_code == 400


def test_migration_adds_columns_and_is_rerunnable():
    spec = importlib.util.spec_from_file_location("m0002", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "CREATE TABLE ai_scene (id INTEGER PRIMARY KEY, tenant_id VARCHAR(32), name VARCHAR(128),"
                " ws_url VARCHAR(512), fallback VARCHAR(16), opening TEXT)"
            )
        )
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            module.upgrade()
        columns = {col["name"] for col in sa.inspect(conn).get_columns("ai_scene")}
    assert {"system_prompt", "transfer_skill_group_id", "asr_json", "tts_json", "llm_json"} <= columns
