from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app


def test_admin_can_post_blacklist_agent_forbidden():
    client = TestClient(app)
    seed_user("admin_bl", "admin-pass", "admin", must_change_password=False)

    admin_login = client.post(
        "/auth/login", json={"username": "admin_bl", "password": "admin-pass"}
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    created = client.post(
        "/blacklist",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": "13800000000", "reason": "test", "tenant_id": "other"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["phone"] == "13800000000"
    assert body.get("tenant_id", "t001") == "t001"

    agent_login = client.post(
        "/auth/login", json={"username": "agent1", "password": "secret-pass"}
    )
    assert agent_login.status_code == 200
    agent_token = agent_login.json()["access_token"]
    denied = client.post(
        "/blacklist",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"phone": "13900000000", "reason": "nope"},
    )
    assert denied.status_code == 403


def test_extension_crud():
    client = TestClient(app)
    seed_user("admin_ext", "admin-pass", "admin", must_change_password=False)
    login = client.post(
        "/auth/login", json={"username": "admin_ext", "password": "admin-pass"}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/extensions",
        headers=headers,
        json={"ext_number": "8001", "display_name": "坐席甲", "enabled": True},
    )
    assert created.status_code == 201
    ext_id = created.json()["id"]

    again = client.post(
        "/extensions",
        headers=headers,
        json={"ext_number": "8001", "display_name": "重复"},
    )
    assert again.status_code == 409

    updated = client.put(
        f"/extensions/{ext_id}",
        headers=headers,
        json={"ext_number": "8002", "display_name": "坐席乙", "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["ext_number"] == "8002"
    assert updated.json()["enabled"] is False

    listed = client.get("/extensions", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == ext_id for item in listed.json()["items"])

    removed = client.delete(f"/extensions/{ext_id}", headers=headers)
    assert removed.status_code == 204
    missing = client.delete(f"/extensions/{ext_id}", headers=headers)
    assert missing.status_code == 404
