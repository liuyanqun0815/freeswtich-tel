# 机器人接听（识别 / 合成 / 大模型抽象）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 呼入「机器人」入口后，机器人用 OpenAI 兼容的识别、大模型、合成接口完成接听、多轮对话、打断、转人工、挂机。

**Architecture:** `ai_gw` 新增 FastAPI WebSocket 服务接收 `mod_audio_stream` 推来的 8k PCM，每通电话一个 `BotSession`：端点检测切句 → 识别 → 大模型流式回复 → 按句合成 → `streamAudio` 回 FS。打断、转人工、挂机写入 Redis 列表 `vr:ai:cmd`，由 `engine` 后台线程取出执行。三个能力各自是接口，适配器按 `vendor` 注册，本期只有 `openai`。

**Tech Stack:** Python 3.12+、FastAPI、httpx、numpy + soxr（重采样）、webrtcvad-wheels（端点检测）、redis-py、SQLAlchemy、Alembic；前端 React + Ant Design。

**设计文档:** `docs/superpowers/specs/2026-10-27-ai-voice-bot-design.md`

## Global Constraints

- Python 代码 black 格式化，`max_line_length=120`；禁止 `from module import *`；变量 snake_case。
- 只有 `engine` 连 ESL；`ai_gw` 不得出现 `8021`，不得直接控制通话。
- 推流与录音等通话进入 `Channel-Call-State=ACTIVE` 后再下发。
- 数据库与接口不存密钥：配置里只有 `api_key_env`，出现 `api_key` 字段返回 400。
- 电话音频统一 8000Hz、16bit、单声道 PCM。
- 测试从仓库根目录运行：`python -m pytest tests/<file> -q`；前端在 `web/` 下运行 `npx vitest --run <file>`。
- 只在本地分支 `feat/ai-voice-bot` 上提交，每个任务结束提交一次，提交信息用中文；不推送，不合并到 `main`。

## 文件总览

| 文件 | 职责 |
|------|------|
| `packages/common/src/vr_common/models.py` | `AiScene` 新增 5 列 |
| `alembic/versions/20261027_0002_ai_scene_providers.py` | 新列迁移（已存在则跳过） |
| `apps/api/src/vr_api/resources.py` | AI 场景增删改查 |
| `packages/common/src/vr_common/redis_keys.py` | `AI_COMMANDS` 键名 |
| `apps/ai_gw/pyproject.toml` | ai_gw 依赖 |
| `apps/ai_gw/src/vr_ai/providers/base.py` | 接口与 `ProviderError` |
| `apps/ai_gw/src/vr_ai/providers/openai_compat.py` | OpenAI 兼容适配器 |
| `apps/ai_gw/src/vr_ai/providers/registry.py` | `vendor` → 适配器 |
| `apps/ai_gw/src/vr_ai/audio.py` | WAV 封装、重采样、端点检测 |
| `apps/ai_gw/src/vr_ai/session.py` | 单通电话对话会话 |
| `apps/ai_gw/src/vr_ai/scene.py` | 从数据库读场景 |
| `apps/ai_gw/src/vr_ai/server.py` | `/fs/stream` WebSocket |
| `apps/ai_gw/src/vr_ai/main.py` | 删除（占位文件） |
| `packages/common/src/vr_common/inbound.py` | 机器人推流命令、精简 `decide_park` |
| `apps/engine/src/vr_engine/main.py` | ACTIVE 后执行队列、放音事件、AI 指令 |
| `apps/engine/src/vr_engine/reader.py` | Redis 指令线程与锁 |
| `deploy/docker-entrypoint.sh`、`docker-compose.yml`、`.env.example` | 部署 |
| `web/src/pages/ResourcePages.tsx` | AI 场景页、号码入口选场景 |

---

### Task 1: AI 场景配置字段、迁移与接口

**Files:**
- Modify: `packages/common/src/vr_common/models.py`（`class AiScene`）
- Create: `alembic/versions/20261027_0002_ai_scene_providers.py`
- Modify: `apps/api/src/vr_api/resources.py`（`AiSceneIn` 与 `# ---- AI scenes` 段落的两个路由）
- Test: `tests/test_ai_scenes.py`

**Interfaces:**
- Produces: `AiScene.system_prompt: str`、`AiScene.transfer_skill_group_id: int | None`、`AiScene.asr_json / tts_json / llm_json: str`（JSON 文本）。
- Produces: `GET/POST /ai-scenes`、`PUT/DELETE /ai-scenes/{scene_id}`；返回体字段 `id, tenant_id, name, opening, fallback, ws_url, system_prompt, transfer_skill_group_id, asr, tts, llm`（后三者为 dict）。

- [ ] **Step 1: 写失败测试** `tests/test_ai_scenes.py`

```python
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app

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
    assert updated.json()["llm"] == {}

    listed = client.get("/ai-scenes", headers=headers).json()["items"]
    assert [item["name"] for item in listed] == ["售后"]

    assert client.delete(f"/ai-scenes/{scene['id']}", headers=headers).status_code == 204
    assert client.delete(f"/ai-scenes/{scene['id']}", headers=headers).status_code == 404


def test_ai_scene_rejects_plain_api_key_and_bad_fallback():
    client = TestClient(app)
    headers = _admin_headers(client)
    with_key = client.post("/ai-scenes", headers=headers, json={"name": "x", "llm": {"api_key": "sk-123"}})
    assert with_key.status_code == 400
    bad_fallback = client.post("/ai-scenes", headers=headers, json={"name": "x", "fallback": "retry"})
    assert bad_fallback.status_code == 400


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
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ai_scenes.py -q`
Expected: FAIL（`405 Method Not Allowed` / 迁移文件不存在）

- [ ] **Step 3: 模型加列** — `packages/common/src/vr_common/models.py` 的 `class AiScene` 替换为：

```python
class AiScene(Base, TenantScoped):
    __tablename__ = "ai_scene"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    ws_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    fallback: Mapped[str] = mapped_column(String(16), nullable=False, default="transfer")
    opening: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, default="")
    transfer_skill_group_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    asr_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    tts_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    llm_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
```

- [ ] **Step 4: 迁移** — 创建 `alembic/versions/20261027_0002_ai_scene_providers.py`：

```python
"""AI scene: provider configs, system prompt, transfer skill group."""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261027_0002"
down_revision: Union[str, None] = "20261021_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns() -> list[sa.Column]:
    return [
        sa.Column("system_prompt", sa.Text(), nullable=False, server_default=""),
        sa.Column("transfer_skill_group_id", sa.Integer(), nullable=True),
        sa.Column("asr_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("tts_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("llm_json", sa.Text(), nullable=False, server_default="{}"),
    ]


def upgrade() -> None:
    # 0001 用当前模型 create_all，新库里这些列已经存在
    existing = {col["name"] for col in sa.inspect(op.get_bind()).get_columns("ai_scene")}
    for column in _columns():
        if column.name not in existing:
            op.add_column("ai_scene", column)


def downgrade() -> None:
    for column in reversed(_columns()):
        op.drop_column("ai_scene", column.name)
```

- [ ] **Step 5: 接口** — `apps/api/src/vr_api/resources.py`

把 `class AiSceneIn` 替换为：

```python
class AiSceneIn(BaseModel):
    name: str
    opening: str = ""
    fallback: str = "transfer"
    system_prompt: str = ""
    transfer_skill_group_id: Optional[int] = None
    asr: dict[str, Any] = Field(default_factory=dict)
    tts: dict[str, Any] = Field(default_factory=dict)
    llm: dict[str, Any] = Field(default_factory=dict)
    ws_url: Optional[str] = None
    tenant_id: Optional[str] = None
```

把现有 `@router.get("/ai-scenes")` 与 `@router.post("/ai-scenes")` 两个函数整体替换为：

```python
def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _scene_out(row: AiScene) -> dict[str, Any]:
    item = _row_dict(row, "name", "ws_url", "fallback", "opening", "system_prompt", "transfer_skill_group_id")
    item["asr"] = _json_dict(row.asr_json)
    item["tts"] = _json_dict(row.tts_json)
    item["llm"] = _json_dict(row.llm_json)
    return item


def _apply_scene(row: AiScene, body: AiSceneIn) -> None:
    if body.fallback not in _ALLOWED_FALLBACKS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="fallback must be transfer or hangup")
    if any("api_key" in config for config in (body.asr, body.tts, body.llm)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="密钥写在 .env，配置里只填 api_key_env",
        )
    row.name = body.name.strip()
    row.opening = body.opening
    row.fallback = body.fallback
    row.system_prompt = body.system_prompt
    row.transfer_skill_group_id = body.transfer_skill_group_id
    row.ws_url = body.ws_url
    row.asr_json = json.dumps(body.asr, ensure_ascii=False)
    row.tts_json = json.dumps(body.tts, ensure_ascii=False)
    row.llm_json = json.dumps(body.llm, ensure_ascii=False)


def _scene_or_404(db: Session, scene_id: int) -> AiScene:
    row = db.get(AiScene, scene_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="AI 场景不存在")
    return row


@router.get("/ai-scenes")
def list_ai_scenes(
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    rows = db.scalars(select(AiScene).where(AiScene.tenant_id == DEFAULT_TENANT).order_by(AiScene.id)).all()
    return {"items": [_scene_out(r) for r in rows]}


@router.post("/ai-scenes")
def create_ai_scene(
    body: AiSceneIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = AiScene(tenant_id=DEFAULT_TENANT)
    _apply_scene(row, body)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _scene_out(row)


@router.put("/ai-scenes/{scene_id}")
def update_ai_scene(
    scene_id: int,
    body: AiSceneIn,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> dict:
    row = _scene_or_404(db, scene_id)
    _apply_scene(row, body)
    db.commit()
    db.refresh(row)
    return _scene_out(row)


@router.delete("/ai-scenes/{scene_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ai_scene(
    scene_id: int,
    db: Annotated[Session, Depends(get_db)],
    _: Annotated[User, Depends(require_admin)],
) -> None:
    db.delete(_scene_or_404(db, scene_id))
    db.commit()
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m pytest tests/test_ai_scenes.py tests/test_models.py tests/test_resources.py -q`
Expected: PASS

---

### Task 2: 接口定义与 OpenAI 兼容大模型适配器

**Files:**
- Modify: `apps/ai_gw/pyproject.toml`
- Create: `apps/ai_gw/src/vr_ai/providers/__init__.py`（空文件）
- Create: `apps/ai_gw/src/vr_ai/providers/base.py`
- Create: `apps/ai_gw/src/vr_ai/providers/openai_compat.py`
- Test: `tests/test_ai_openai_llm.py`

**Interfaces:**
- Produces: `ProviderError(RuntimeError)`；协议 `AsrSession.feed(pcm: bytes) -> None`、`async AsrSession.finish() -> str`、`Asr.open() -> AsrSession`、`Tts.stream(text: str) -> AsyncIterator[bytes]`、`Llm.stream(messages: list[dict]) -> AsyncIterator[dict]`。
- Produces: `OpenAiLlm(config: dict, transport: httpx.AsyncBaseTransport | None = None)`，产出 `{"text": str}` 或 `{"action": "transfer" | "hangup"}`。
- Produces（供 Task 3 复用）: `openai_compat._Endpoint(config, transport)`（属性 `base_url`、`headers`，方法 `client()`），`openai_compat._raise_for_status(resp, kind)`。

- [ ] **Step 1: 依赖** — `apps/ai_gw/pyproject.toml` 替换为：

```toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "vr-ai"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "httpx>=0.27",
    "numpy>=1.26",
    "soxr>=0.5",
    "webrtcvad-wheels>=2.0.14",
    "redis>=5.0",
    "sqlalchemy>=2.0",
    "psycopg2-binary>=2.9",
    "vr-common",
]

[tool.setuptools.packages.find]
where = ["src"]
```

Run: `python -m pip install -e packages/common -e apps/ai_gw`
Expected: `Successfully installed ... soxr ... webrtcvad-wheels ...`

- [ ] **Step 2: 写失败测试** `tests/test_ai_openai_llm.py`

```python
import asyncio
import json

import httpx
import pytest

from vr_ai.providers.base import ProviderError
from vr_ai.providers.openai_compat import OpenAiLlm


def _sse(*chunks: dict) -> bytes:
    lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def _collect(llm: OpenAiLlm, messages: list[dict]) -> list[dict]:
    async def run() -> list[dict]:
        return [item async for item in llm.stream(messages)]

    return asyncio.run(run())


def test_llm_streams_text_then_tool_action(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        body = _sse(
            {"choices": [{"delta": {"content": "好的，"}}]},
            {"choices": [{"delta": {"content": "马上转接。"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "transfer_to_agent"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]},
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = OpenAiLlm(
        {"base_url": "https://llm.example/v1/", "api_key_env": "TEST_LLM_KEY", "model": "m1"},
        transport=httpx.MockTransport(handler),
    )
    items = _collect(llm, [{"role": "user", "content": "转人工"}])

    assert items == [{"text": "好的，"}, {"text": "马上转接。"}, {"action": "transfer"}]
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["stream"] is True
    assert {tool["function"]["name"] for tool in seen["body"]["tools"]} == {"transfer_to_agent", "hangup"}


def test_llm_http_error_raises_provider_error():
    llm = OpenAiLlm(
        {"base_url": "https://llm.example/v1", "model": "m1"},
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="bad key")),
    )
    with pytest.raises(ProviderError, match="401"):
        _collect(llm, [])


def test_missing_key_env_is_rejected(monkeypatch):
    monkeypatch.delenv("NOT_SET_KEY", raising=False)
    with pytest.raises(ProviderError, match="NOT_SET_KEY"):
        OpenAiLlm({"base_url": "https://llm.example/v1", "api_key_env": "NOT_SET_KEY"})


def test_missing_base_url_is_rejected():
    with pytest.raises(ProviderError, match="base_url"):
        OpenAiLlm({"model": "m1"})
```

- [ ] **Step 3: 运行确认失败**

Run: `python -m pytest tests/test_ai_openai_llm.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'vr_ai.providers'`）

- [ ] **Step 4: 接口** — 创建空文件 `apps/ai_gw/src/vr_ai/providers/__init__.py`，再创建 `apps/ai_gw/src/vr_ai/providers/base.py`：

```python
"""Vendor-neutral speech recognition, speech synthesis and dialog interfaces."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class ProviderError(RuntimeError):
    """A vendor call failed; the session falls back per scene config."""


class AsrSession(Protocol):
    def feed(self, pcm: bytes) -> None: ...

    async def finish(self) -> str: ...


class Asr(Protocol):
    def open(self) -> AsrSession: ...


class Tts(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]: ...


class Llm(Protocol):
    def stream(self, messages: list[dict]) -> AsyncIterator[dict]: ...
```

- [ ] **Step 5: 大模型适配器** — 创建 `apps/ai_gw/src/vr_ai/providers/openai_compat.py`：

```python
"""Adapters for OpenAI-compatible HTTP APIs (DeepSeek, Qwen, SiliconFlow, Ollama, vLLM...)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from vr_ai.providers.base import ProviderError

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "transfer_to_agent",
            "description": "用户要求人工服务，或问题超出你的处理范围时调用",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hangup",
            "description": "对话已经结束、用户道别时调用",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
_TOOL_ACTIONS = {"transfer_to_agent": "transfer", "hangup": "hangup"}


class _Endpoint:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None) -> None:
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        if not self.base_url:
            raise ProviderError("base_url is required")
        key_env = str(config.get("api_key_env") or "")
        key = os.environ.get(key_env, "") if key_env else ""
        if key_env and not key:
            raise ProviderError(f"environment variable {key_env} is empty")
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._timeout = float(config.get("timeout") or 10)
        self._transport = transport

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)


async def _raise_for_status(resp: httpx.Response, kind: str) -> None:
    if resp.status_code >= 400:
        detail = (await resp.aread()).decode("utf-8", errors="replace")[:300]
        raise ProviderError(f"{kind} http {resp.status_code}: {detail}")


class OpenAiLlm:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._endpoint = _Endpoint(config, transport)
        self._model = str(config.get("model") or "")
        self._temperature = config.get("temperature")

    async def stream(self, messages: list[dict]) -> AsyncIterator[dict]:
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "stream": True, "tools": _TOOLS}
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        tool_names: dict[int, str] = {}
        url = f"{self._endpoint.base_url}/chat/completions"
        try:
            async with self._endpoint.client() as client:
                async with client.stream("POST", url, json=payload, headers=self._endpoint.headers) as resp:
                    await _raise_for_status(resp, "llm")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        choices = json.loads(data).get("choices") or [{}]
                        delta = choices[0].get("delta") or {}
                        if delta.get("content"):
                            yield {"text": delta["content"]}
                        for call in delta.get("tool_calls") or []:
                            name = (call.get("function") or {}).get("name")
                            if name:
                                tool_names[int(call.get("index") or 0)] = name
        except httpx.HTTPError as exc:
            raise ProviderError(f"llm request failed: {exc}") from exc
        for name in tool_names.values():
            if name in _TOOL_ACTIONS:
                yield {"action": _TOOL_ACTIONS[name]}
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m pytest tests/test_ai_openai_llm.py -q`
Expected: PASS（4 passed）

---

### Task 3: 音频工具、识别与合成适配器、注册表

**Files:**
- Create: `apps/ai_gw/src/vr_ai/audio.py`
- Modify: `apps/ai_gw/src/vr_ai/providers/openai_compat.py`（追加 `OpenAiAsr`、`OpenAiTts`）
- Create: `apps/ai_gw/src/vr_ai/providers/registry.py`
- Test: `tests/test_ai_audio.py`、`tests/test_ai_openai_audio.py`

**Interfaces:**
- Consumes: Task 2 的 `_Endpoint`、`_raise_for_status`、`ProviderError`、`AsrSession`。
- Produces: `audio.PHONE_RATE = 8000`、`audio.FRAME_MS = 20`、`audio.FRAME_BYTES = 320`、`audio.to_wav(pcm: bytes, rate: int = 8000) -> bytes`、`audio.duration_seconds(pcm: bytes, rate: int = 8000) -> float`、`audio.StreamResampler(src_rate: int)`（`push(chunk: bytes) -> bytes`、`flush() -> bytes`）。
- Produces: `OpenAiAsr(config, transport=None)`（`open() -> AsrSession`）、`OpenAiTts(config, transport=None)`（`stream(text) -> AsyncIterator[bytes]`，8k PCM16）。
- Produces: `registry.build_asr(config: dict) -> Asr`、`build_tts(config) -> Tts`、`build_llm(config) -> Llm`；未知 `vendor` 抛 `ProviderError`。

- [ ] **Step 1: 写失败测试** `tests/test_ai_audio.py`

```python
import io
import wave

import numpy as np

from vr_ai.audio import StreamResampler, duration_seconds, to_wav


def test_stream_resampler_downsamples_across_odd_chunks():
    src = (np.sin(np.arange(2400) / 10) * 8000).astype(np.int16).tobytes()  # 0.1 秒 24k
    resampler = StreamResampler(24000)
    out = resampler.push(src[:1001]) + resampler.push(src[1001:]) + resampler.flush()
    assert abs(len(out) // 2 - 800) <= 16


def test_stream_resampler_passthrough_keeps_whole_samples():
    resampler = StreamResampler(8000)
    assert resampler.push(b"\x01\x02\x03") == b"\x01\x02"
    assert resampler.push(b"\x04") == b"\x03\x04"
    assert resampler.flush() == b""


def test_to_wav_and_duration():
    wav = to_wav(b"\x00\x00" * 8000)
    assert wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav)) as reader:
        assert reader.getframerate() == 8000
        assert reader.getnframes() == 8000
    assert duration_seconds(b"\x00\x00" * 4000) == 0.5
```

`tests/test_ai_openai_audio.py`：

```python
import asyncio
import json

import httpx
import pytest

from vr_ai.providers.base import ProviderError
from vr_ai.providers.openai_compat import OpenAiAsr, OpenAiLlm, OpenAiTts
from vr_ai.providers.registry import build_asr, build_llm, build_tts


def test_asr_posts_wav_once_per_utterance():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"text": " 你好 "})

    asr = OpenAiAsr(
        {"base_url": "https://asr.example/v1", "model": "whisper-1", "language": "zh"},
        transport=httpx.MockTransport(handler),
    )
    session = asr.open()
    session.feed(b"\x00\x00" * 800)
    session.feed(b"\x00\x00" * 800)
    assert asyncio.run(session.finish()) == "你好"
    assert seen["url"] == "https://asr.example/v1/audio/transcriptions"
    assert b"RIFF" in seen["body"]
    assert b'name="language"' in seen["body"]


def test_asr_without_audio_skips_request():
    asr = OpenAiAsr(
        {"base_url": "https://asr.example/v1"},
        transport=httpx.MockTransport(lambda request: pytest.fail("no request expected")),
    )
    assert asyncio.run(asr.open().finish()) == ""


def test_asr_http_error_raises_provider_error():
    asr = OpenAiAsr(
        {"base_url": "https://asr.example/v1"},
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom")),
    )
    session = asr.open()
    session.feed(b"\x00\x00" * 10)
    with pytest.raises(ProviderError, match="500"):
        asyncio.run(session.finish())


def test_tts_requests_pcm_and_resamples_to_8k():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"\x00\x00" * 2400)

    tts = OpenAiTts(
        {"base_url": "https://tts.example/v1", "model": "tts-1", "voice": "alloy", "sample_rate": 24000},
        transport=httpx.MockTransport(handler),
    )

    async def run() -> bytes:
        return b"".join([chunk async for chunk in tts.stream("你好")])

    out = asyncio.run(run())
    assert abs(len(out) // 2 - 800) <= 16
    assert seen["url"] == "https://tts.example/v1/audio/speech"
    assert seen["body"] == {"model": "tts-1", "voice": "alloy", "input": "你好", "response_format": "pcm"}


def test_registry_builds_openai_and_rejects_unknown_vendor():
    assert isinstance(build_asr({"vendor": "openai", "base_url": "https://x/v1"}), OpenAiAsr)
    assert isinstance(build_tts({"vendor": "openai", "base_url": "https://x/v1"}), OpenAiTts)
    assert isinstance(build_llm({"vendor": "openai", "base_url": "https://x/v1"}), OpenAiLlm)
    with pytest.raises(ProviderError, match="aliyun"):
        build_asr({"vendor": "aliyun"})
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ai_audio.py tests/test_ai_openai_audio.py -q`
Expected: FAIL（`No module named 'vr_ai.audio'`）

- [ ] **Step 3: 音频工具** — 创建 `apps/ai_gw/src/vr_ai/audio.py`：

```python
"""8k telephone PCM helpers: WAV framing and resampling."""

from __future__ import annotations

import io
import wave

import numpy as np
import soxr

PHONE_RATE = 8000
FRAME_MS = 20
FRAME_BYTES = PHONE_RATE * FRAME_MS // 1000 * 2


def to_wav(pcm: bytes, rate: int = PHONE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return buf.getvalue()


def duration_seconds(pcm: bytes, rate: int = PHONE_RATE) -> float:
    return len(pcm) / (2 * rate)


class StreamResampler:
    """Resample a PCM16 byte stream to 8k across arbitrary chunk boundaries."""

    def __init__(self, src_rate: int) -> None:
        self._stream = None if src_rate == PHONE_RATE else soxr.ResampleStream(src_rate, PHONE_RATE, 1, dtype="int16")
        self._carry = b""

    def push(self, chunk: bytes) -> bytes:
        data = self._carry + chunk
        usable = len(data) - len(data) % 2
        self._carry = data[usable:]
        if self._stream is None:
            return data[:usable]
        samples = np.frombuffer(data[:usable], dtype=np.int16)
        return self._stream.resample_chunk(samples).tobytes()

    def flush(self) -> bytes:
        if self._stream is None:
            return b""
        return self._stream.resample_chunk(np.zeros(0, dtype=np.int16), last=True).tobytes()
```

- [ ] **Step 4: 识别与合成适配器** — 在 `openai_compat.py` 顶部 import 区追加：

```python
from vr_ai.audio import StreamResampler, to_wav
from vr_ai.providers.base import AsrSession
```

文件末尾追加：

```python
class _BufferedAsrSession:
    def __init__(self, asr: "OpenAiAsr") -> None:
        self._asr = asr
        self._pcm = bytearray()

    def feed(self, pcm: bytes) -> None:
        self._pcm.extend(pcm)

    async def finish(self) -> str:
        return await self._asr.transcribe(bytes(self._pcm))


class OpenAiAsr:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._endpoint = _Endpoint(config, transport)
        self._model = str(config.get("model") or "whisper-1")
        self._language = str(config.get("language") or "")

    def open(self) -> AsrSession:
        return _BufferedAsrSession(self)

    async def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        form = {"model": self._model}
        if self._language:
            form["language"] = self._language
        files = {"file": ("speech.wav", to_wav(pcm), "audio/wav")}
        url = f"{self._endpoint.base_url}/audio/transcriptions"
        try:
            async with self._endpoint.client() as client:
                resp = await client.post(url, data=form, files=files, headers=self._endpoint.headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"asr request failed: {exc}") from exc
        await _raise_for_status(resp, "asr")
        return str(resp.json().get("text") or "").strip()


class OpenAiTts:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._endpoint = _Endpoint(config, transport)
        self._model = str(config.get("model") or "tts-1")
        self._voice = str(config.get("voice") or "alloy")
        self._sample_rate = int(config.get("sample_rate") or 24000)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        payload = {"model": self._model, "voice": self._voice, "input": text, "response_format": "pcm"}
        resampler = StreamResampler(self._sample_rate)
        url = f"{self._endpoint.base_url}/audio/speech"
        try:
            async with self._endpoint.client() as client:
                async with client.stream("POST", url, json=payload, headers=self._endpoint.headers) as resp:
                    await _raise_for_status(resp, "tts")
                    async for chunk in resp.aiter_bytes():
                        out = resampler.push(chunk)
                        if out:
                            yield out
        except httpx.HTTPError as exc:
            raise ProviderError(f"tts request failed: {exc}") from exc
        tail = resampler.flush()
        if tail:
            yield tail
```

- [ ] **Step 5: 注册表** — 创建 `apps/ai_gw/src/vr_ai/providers/registry.py`：

```python
"""Pick a vendor adapter from a scene's provider config."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vr_ai.providers.base import Asr, Llm, ProviderError, Tts
from vr_ai.providers.openai_compat import OpenAiAsr, OpenAiLlm, OpenAiTts

_ASR: dict[str, Callable[[dict[str, Any]], Asr]] = {"openai": OpenAiAsr}
_TTS: dict[str, Callable[[dict[str, Any]], Tts]] = {"openai": OpenAiTts}
_LLM: dict[str, Callable[[dict[str, Any]], Llm]] = {"openai": OpenAiLlm}


def _build(table: dict[str, Callable[[dict[str, Any]], Any]], kind: str, config: dict[str, Any]) -> Any:
    vendor = str(config.get("vendor") or "")
    factory = table.get(vendor)
    if factory is None:
        raise ProviderError(f"{kind} vendor not supported: {vendor or '-'}")
    return factory(config)


def build_asr(config: dict[str, Any]) -> Asr:
    return _build(_ASR, "asr", config)


def build_tts(config: dict[str, Any]) -> Tts:
    return _build(_TTS, "tts", config)


def build_llm(config: dict[str, Any]) -> Llm:
    return _build(_LLM, "llm", config)
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m pytest tests/test_ai_audio.py tests/test_ai_openai_audio.py tests/test_ai_openai_llm.py -q`
Expected: PASS

---

### Task 4: 端点检测

**Files:**
- Modify: `apps/ai_gw/src/vr_ai/audio.py`（追加 `Endpointer`）
- Test: `tests/test_ai_endpointer.py`

**Interfaces:**
- Produces: `Endpointer(*, aggressiveness: int = 2, start_ms: int = 200, end_ms: int = 600, max_ms: int = 15000, is_speech: Callable[[bytes, int], bool] | None = None)`；`push(pcm: bytes) -> list[tuple[str, bytes]]`，事件为 `("start", b"")`、`("audio", 20ms 帧)`、`("end", b"")`。

- [ ] **Step 1: 写失败测试** `tests/test_ai_endpointer.py`

```python
from vr_ai.audio import Endpointer

VOICED = b"\x01\x00" * 160
SILENT = bytes(320)


def _endpointer(**kwargs) -> Endpointer:
    return Endpointer(is_speech=lambda frame, rate: frame[0] != 0, **kwargs)


def _kinds(events: list[tuple[str, bytes]]) -> list[str]:
    return [kind for kind, _ in events]


def test_start_after_200ms_voice_and_end_after_600ms_silence():
    endpointer = _endpointer()
    assert endpointer.push(VOICED * 9) == []
    started = _kinds(endpointer.push(VOICED))
    assert started[0] == "start"
    assert started.count("audio") == 10
    assert "end" not in _kinds(endpointer.push(SILENT * 29))
    assert _kinds(endpointer.push(SILENT))[-1] == "end"


def test_short_blip_is_ignored():
    assert _endpointer().push(VOICED * 5 + SILENT + VOICED * 5) == []


def test_long_speech_is_cut_at_max_length():
    kinds = _kinds(_endpointer(max_ms=1000).push(VOICED * 60))
    assert kinds.count("end") == 1


def test_frames_split_across_pushes():
    endpointer = _endpointer()
    assert endpointer.push(VOICED[:100]) == []
    assert _kinds(endpointer.push(VOICED[100:] + VOICED * 9))[0] == "start"


def test_real_vad_treats_silence_as_silence():
    assert Endpointer().push(SILENT * 50) == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ai_endpointer.py -q`
Expected: FAIL（`ImportError: cannot import name 'Endpointer'`）

- [ ] **Step 3: 实现** — `audio.py` import 区追加：

```python
from collections.abc import Callable

import webrtcvad
```

文件末尾追加：

```python
class Endpointer:
    """Turn 8k PCM into speech start, 20ms speech frames and utterance end events."""

    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        start_ms: int = 200,
        end_ms: int = 600,
        max_ms: int = 15000,
        is_speech: Callable[[bytes, int], bool] | None = None,
    ) -> None:
        self._is_speech = is_speech or webrtcvad.Vad(aggressiveness).is_speech
        self._start_frames = start_ms // FRAME_MS
        self._end_frames = end_ms // FRAME_MS
        self._max_frames = max_ms // FRAME_MS
        self._buf = bytearray()
        self._pending: list[bytes] = []
        self._in_speech = False
        self._silent = 0
        self._frames = 0

    def push(self, pcm: bytes) -> list[tuple[str, bytes]]:
        self._buf.extend(pcm)
        events: list[tuple[str, bytes]] = []
        while len(self._buf) >= FRAME_BYTES:
            frame = bytes(self._buf[:FRAME_BYTES])
            del self._buf[:FRAME_BYTES]
            events.extend(self._frame(frame))
        return events

    def _frame(self, frame: bytes) -> list[tuple[str, bytes]]:
        voiced = self._is_speech(frame, PHONE_RATE)
        if not self._in_speech:
            if not voiced:
                self._pending = []
                return []
            self._pending.append(frame)
            if len(self._pending) < self._start_frames:
                return []
            self._in_speech = True
            self._silent = 0
            self._frames = len(self._pending)
            started = [("start", b"")] + [("audio", item) for item in self._pending]
            self._pending = []
            return started
        self._frames += 1
        self._silent = 0 if voiced else self._silent + 1
        events = [("audio", frame)]
        if self._silent >= self._end_frames or self._frames >= self._max_frames:
            self._in_speech = False
            events.append(("end", b""))
        return events
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ai_endpointer.py -q`
Expected: PASS（5 passed）

---

### Task 5: 对话会话

**Files:**
- Create: `apps/ai_gw/src/vr_ai/session.py`
- Test: `tests/test_ai_session.py`

**Interfaces:**
- Consumes: Task 2 接口、Task 3 `to_wav`、`duration_seconds`、Task 4 `Endpointer`、已有 `vr_ai.scriptbot.opening_text`。
- Produces: `SceneConfig(scene_id: str, opening: str = "", system_prompt: str = "", fallback: str = "transfer", transfer_skill_group_id: int | None = None)`。
- Produces: `fallback_action(scene) -> str`、`command_for(scene, action: str) -> tuple[str, dict]`（`transfer` 带 `skill_group_id`）。
- Produces: `BotSession(*, scene, asr, tts, llm, send_audio, send_command, endpointer=None, clock=time.monotonic, sleep=asyncio.sleep)`；`async start()`、`async on_audio(pcm)`、`async drain()`、`async close()`、属性 `finished: bool`。`send_audio(wav: bytes)`、`send_command(kind: str, extra: dict)` 均为 async。

- [ ] **Step 1: 写失败测试** `tests/test_ai_session.py`

```python
import asyncio

from vr_ai.audio import Endpointer
from vr_ai.providers.base import ProviderError
from vr_ai.session import BotSession, SceneConfig, command_for, split_sentences

VOICED = b"\x01\x00" * 160
SILENT = bytes(320)
ONE_SECOND = b"\x00\x00" * 8000


class FakeAsrSession:
    def __init__(self, text: str, error: Exception | None) -> None:
        self._text = text
        self._error = error

    def feed(self, pcm: bytes) -> None:
        return None

    async def finish(self) -> str:
        if self._error is not None:
            raise self._error
        return self._text


class FakeAsr:
    def __init__(self, *texts: str, error: Exception | None = None) -> None:
        self._texts = list(texts)
        self._error = error

    def open(self) -> FakeAsrSession:
        return FakeAsrSession(self._texts.pop(0) if self._texts else "", self._error)


class FakeTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def stream(self, text: str):
        self.texts.append(text)
        yield ONE_SECOND


class FakeLlm:
    def __init__(self, *items: dict) -> None:
        self._items = list(items)
        self.calls: list[list[dict]] = []

    async def stream(self, messages: list[dict]):
        self.calls.append(messages)
        for item in self._items:
            yield item


class Harness:
    def __init__(self, scene: SceneConfig, asr: FakeAsr | None = None, llm: FakeLlm | None = None) -> None:
        self.audio: list[bytes] = []
        self.commands: list[tuple[str, dict]] = []
        self.sleeps: list[float] = []
        self.tts = FakeTts()
        self.llm = llm or FakeLlm()

        async def send_audio(wav: bytes) -> None:
            self.audio.append(wav)

        async def send_command(kind: str, extra: dict) -> None:
            self.commands.append((kind, extra))

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.session = BotSession(
            scene=scene,
            asr=asr or FakeAsr(),
            tts=self.tts,
            llm=self.llm,
            send_audio=send_audio,
            send_command=send_command,
            endpointer=Endpointer(is_speech=lambda frame, rate: frame[0] != 0),
            clock=lambda: 0.0,
            sleep=sleep,
        )

    async def say(self) -> None:
        await self.session.on_audio(VOICED * 10 + SILENT * 30)
        await self.session.drain()


def _run(coro_factory):
    return asyncio.run(coro_factory())


def test_split_sentences_keeps_unfinished_tail():
    assert split_sentences("好的。请稍等，") == (["好的。"], "请稍等，")


def test_command_for_transfer_carries_skill_group():
    scene = SceneConfig(scene_id="1", transfer_skill_group_id=7)
    assert command_for(scene, "transfer") == ("transfer", {"skill_group_id": 7})
    assert command_for(scene, "hangup") == ("hangup", {})


def test_opening_gets_assistant_prefix_and_is_sent_as_wav():
    async def run():
        harness = Harness(SceneConfig(scene_id="1", opening="您好"))
        await harness.session.start()
        return harness

    harness = _run(run)
    assert harness.tts.texts == ["我是智能助理。您好"]
    assert harness.audio[0][:4] == b"RIFF"


def test_utterance_is_answered_sentence_by_sentence():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", system_prompt="你是客服"),
            asr=FakeAsr("查订单"),
            llm=FakeLlm({"text": "好的。请"}, {"text": "稍等"}),
        )
        await harness.say()
        return harness

    harness = _run(run)
    assert harness.tts.texts == ["好的。", "请稍等"]
    assert harness.llm.calls[0] == [
        {"role": "system", "content": "你是客服"},
        {"role": "user", "content": "查订单"},
    ]
    assert len(harness.audio) == 2
    assert harness.commands == []


def test_speaking_over_the_robot_sends_break():
    async def run():
        harness = Harness(SceneConfig(scene_id="1", opening="我是智能助理，您好"))
        await harness.session.start()
        await harness.session.on_audio(VOICED * 10)
        return harness

    assert _run(run).commands == [("break", {})]


def test_transfer_waits_for_playback_then_hands_over():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", transfer_skill_group_id=7),
            asr=FakeAsr("转人工"),
            llm=FakeLlm({"text": "正在为您转接。"}, {"action": "transfer"}),
        )
        await harness.say()
        return harness

    harness = _run(run)
    assert harness.sleeps == [1.0]
    assert harness.commands == [("transfer", {"skill_group_id": 7})]
    assert harness.session.finished


def test_provider_error_falls_back_per_scene():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", fallback="hangup"),
            asr=FakeAsr(error=ProviderError("asr down")),
        )
        await harness.say()
        return harness

    assert _run(run).commands == [("hangup", {})]


def test_empty_transcript_is_ignored():
    async def run():
        harness = Harness(SceneConfig(scene_id="1"), asr=FakeAsr(""))
        await harness.say()
        return harness

    harness = _run(run)
    assert harness.llm.calls == []
    assert harness.commands == []
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ai_session.py -q`
Expected: FAIL（`No module named 'vr_ai.session'`）

- [ ] **Step 3: 实现** — 创建 `apps/ai_gw/src/vr_ai/session.py`：

```python
"""One robot conversation: listen, think, speak, then hand control back to engine."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from vr_ai.audio import Endpointer, duration_seconds, to_wav
from vr_ai.providers.base import Asr, AsrSession, Llm, ProviderError, Tts
from vr_ai.scriptbot import opening_text

logger = logging.getLogger("vr.ai.session")

_SENTENCE_END = "。！？；!?;\n"

SendAudio = Callable[[bytes], Awaitable[None]]
SendCommand = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class SceneConfig:
    scene_id: str
    opening: str = ""
    system_prompt: str = ""
    fallback: str = "transfer"
    transfer_skill_group_id: int | None = None


def fallback_action(scene: SceneConfig) -> str:
    return "transfer" if scene.fallback == "transfer" else "hangup"


def command_for(scene: SceneConfig, action: str) -> tuple[str, dict[str, Any]]:
    if action == "transfer":
        return "transfer", {"skill_group_id": scene.transfer_skill_group_id}
    return "hangup", {}


def split_sentences(text: str) -> tuple[list[str], str]:
    sentences: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_END:
            sentence = text[start : index + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 1
    return sentences, text[start:]


class BotSession:
    def __init__(
        self,
        *,
        scene: SceneConfig,
        asr: Asr,
        tts: Tts,
        llm: Llm,
        send_audio: SendAudio,
        send_command: SendCommand,
        endpointer: Endpointer | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._scene = scene
        self._asr = asr
        self._tts = tts
        self._llm = llm
        self._send_audio = send_audio
        self._send_command = send_command
        self._endpointer = endpointer or Endpointer()
        self._clock = clock
        self._sleep = sleep
        self._history: list[dict[str, str]] = []
        if scene.system_prompt:
            self._history.append({"role": "system", "content": scene.system_prompt})
        self._asr_session: AsrSession | None = None
        self._reply: asyncio.Task[None] | None = None
        self._speaking_until = 0.0
        self.finished = False

    async def start(self) -> None:
        opening = self._scene.opening.strip()
        if not opening:
            return
        if "智能助理" not in opening:
            opening = opening_text(opening)
        self._history.append({"role": "assistant", "content": opening})
        try:
            await self._speak(opening)
        except ProviderError as exc:
            logger.error("opening failed scene=%s: %s", self._scene.scene_id, exc)
            await self._finish(fallback_action(self._scene))

    async def on_audio(self, pcm: bytes) -> None:
        if self.finished:
            return
        for kind, frame in self._endpointer.push(pcm):
            if kind == "start":
                await self._interrupt()
                self._asr_session = self._asr.open()
            elif kind == "audio" and self._asr_session is not None:
                self._asr_session.feed(frame)
            elif kind == "end" and self._asr_session is not None:
                session, self._asr_session = self._asr_session, None
                self._reply = asyncio.create_task(self._answer(session))

    async def drain(self) -> None:
        if self._reply is None:
            return
        for result in await asyncio.gather(self._reply, return_exceptions=True):
            if isinstance(result, Exception):
                logger.warning("reply failed scene=%s: %r", self._scene.scene_id, result)

    async def close(self) -> None:
        if self._reply is not None and not self._reply.done():
            self._reply.cancel()
        await self.drain()

    async def _interrupt(self) -> None:
        if self._reply is not None and not self._reply.done():
            self._reply.cancel()
        if self._clock() < self._speaking_until:
            self._speaking_until = 0.0
            await self._send_command("break", {})

    async def _answer(self, session: AsrSession) -> None:
        try:
            text = await session.finish()
            if not text:
                return
            self._history.append({"role": "user", "content": text})
            action = await self._reply_to(list(self._history))
            if action:
                await self._finish(action)
        except ProviderError as exc:
            logger.error("provider failed scene=%s: %s", self._scene.scene_id, exc)
            await self._finish(fallback_action(self._scene))

    async def _reply_to(self, messages: list[dict[str, str]]) -> str | None:
        spoken: list[str] = []
        action: str | None = None
        pending = ""
        try:
            async for item in self._llm.stream(messages):
                if "action" in item:
                    action = str(item["action"])
                    continue
                sentences, pending = split_sentences(pending + str(item.get("text") or ""))
                for sentence in sentences:
                    await self._speak(sentence)
                    spoken.append(sentence)
            if pending.strip():
                await self._speak(pending.strip())
                spoken.append(pending.strip())
        finally:
            if spoken:
                self._history.append({"role": "assistant", "content": "".join(spoken)})
        return action

    async def _speak(self, text: str) -> None:
        pcm = b"".join([chunk async for chunk in self._tts.stream(text)])
        if not pcm:
            return
        await self._send_audio(to_wav(pcm))
        self._speaking_until = max(self._clock(), self._speaking_until) + duration_seconds(pcm)

    async def _finish(self, action: str) -> None:
        wait = self._speaking_until - self._clock()
        if wait > 0:
            await self._sleep(wait)
        kind, extra = command_for(self._scene, action)
        self.finished = True
        await self._send_command(kind, extra)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_ai_session.py tests/test_scriptbot.py -q`
Expected: PASS

---

### Task 6: ai_gw WebSocket 服务与部署

**Files:**
- Modify: `packages/common/src/vr_common/redis_keys.py`
- Create: `apps/ai_gw/src/vr_ai/scene.py`
- Create: `apps/ai_gw/src/vr_ai/server.py`
- Delete: `apps/ai_gw/src/vr_ai/main.py`
- Modify: `deploy/docker-entrypoint.sh`、`docker-compose.yml`（`ai_gw` 服务）、`.env.example`
- Test: `tests/test_ai_server.py`

**Interfaces:**
- Consumes: Task 1 `AiScene` 新列、Task 3 `build_asr / build_tts / build_llm`、Task 5 `BotSession`、`SceneConfig`、`fallback_action`、`command_for`。
- Produces: `redis_keys.AI_COMMANDS = "vr:ai:cmd"`；Redis 消息 `{"type": "break"|"transfer"|"hangup", "call_id": str, "uuid": str, "skill_group_id"?: int | None}`。
- Produces: `scene.SceneBundle(scene, asr, tts, llm)`、`scene.load_scene(db, scene_id: str) -> SceneBundle | None`。
- Produces: `server.app`，路由 `WS /fs/stream`；模块级可替换函数 `find_scene(scene_id)`、`command_sink(call_id, uuid)`；`streamAudio` 消息 `{"type":"streamAudio","data":{"audioDataType":"wav","sampleRate":8000,"audioData":<base64>}}`。

- [ ] **Step 1: 写失败测试** `tests/test_ai_server.py`

```python
import base64
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import vr_ai.server as server
from vr_ai.providers.base import ProviderError
from vr_ai.scene import SceneBundle
from vr_ai.session import SceneConfig


class FakeTts:
    async def stream(self, text: str):
        yield b"\x00\x00" * 800


def _install(monkeypatch, bundle: SceneBundle | None, *, llm_error: bool = False) -> list[tuple]:
    commands: list[tuple] = []

    def build_llm(config):
        if llm_error:
            raise ProviderError("bad llm config")
        return object()

    def sink(call_id: str, uuid: str):
        async def send(kind: str, extra: dict) -> None:
            commands.append((call_id, uuid, kind, extra))

        return send

    monkeypatch.setattr(server, "find_scene", lambda scene_id: bundle)
    monkeypatch.setattr(server, "build_asr", lambda config: object())
    monkeypatch.setattr(server, "build_tts", lambda config: FakeTts())
    monkeypatch.setattr(server, "build_llm", build_llm)
    monkeypatch.setattr(server, "command_sink", sink)
    return commands


def _bundle(**scene) -> SceneBundle:
    return SceneBundle(scene=SceneConfig(scene_id="3", **scene), asr={}, tts={}, llm={})


def test_stream_speaks_opening_back_as_stream_audio(monkeypatch):
    commands = _install(monkeypatch, _bundle(opening="我是智能助理，您好"))
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "3"}))
        message = json.loads(ws.receive_text())
    assert message["type"] == "streamAudio"
    assert message["data"]["audioDataType"] == "wav"
    assert message["data"]["sampleRate"] == 8000
    assert base64.b64decode(message["data"]["audioData"])[:4] == b"RIFF"
    assert commands == []


def test_unknown_scene_hangs_up(monkeypatch):
    commands = _install(monkeypatch, None)
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "404"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert commands == [("c1", "u1", "hangup", {})]


def test_bad_provider_config_uses_scene_fallback(monkeypatch):
    commands = _install(monkeypatch, _bundle(fallback="transfer", transfer_skill_group_id=7), llm_error=True)
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "3"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert commands == [("c1", "u1", "transfer", {"skill_group_id": 7})]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_ai_server.py -q`
Expected: FAIL（`No module named 'vr_ai.server'`）

- [ ] **Step 3: Redis 键名** — `packages/common/src/vr_common/redis_keys.py` 末尾追加：

```python
AI_COMMANDS = "vr:ai:cmd"
```

- [ ] **Step 4: 场景读取** — 创建 `apps/ai_gw/src/vr_ai/scene.py`：

```python
"""Load one AI scene and its provider configs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from vr_ai.session import SceneConfig
from vr_common.db import DEFAULT_TENANT
from vr_common.models import AiScene


@dataclass(frozen=True)
class SceneBundle:
    scene: SceneConfig
    asr: dict[str, Any]
    tts: dict[str, Any]
    llm: dict[str, Any]


def _config(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def load_scene(db: Session, scene_id: str) -> SceneBundle | None:
    if not scene_id.isdigit():
        return None
    row = db.get(AiScene, int(scene_id))
    if row is None or row.tenant_id != DEFAULT_TENANT:
        return None
    scene = SceneConfig(
        scene_id=scene_id,
        opening=row.opening or "",
        system_prompt=row.system_prompt or "",
        fallback=row.fallback or "transfer",
        transfer_skill_group_id=row.transfer_skill_group_id,
    )
    return SceneBundle(scene=scene, asr=_config(row.asr_json), tts=_config(row.tts_json), llm=_config(row.llm_json))
```

- [ ] **Step 5: WebSocket 服务** — 创建 `apps/ai_gw/src/vr_ai/server.py`：

```python
"""ai_gw: mod_audio_stream WebSocket endpoint running one BotSession per call."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

import redis.asyncio as redis_async
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vr_ai.providers.base import ProviderError
from vr_ai.providers.registry import build_asr, build_llm, build_tts
from vr_ai.scene import SceneBundle, load_scene
from vr_ai.session import BotSession, SendCommand, command_for, fallback_action
from vr_common.db import get_database_url
from vr_common.redis_keys import AI_COMMANDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vr.ai")

app = FastAPI()
_sessions: sessionmaker[Session] | None = None
_redis: redis_async.Redis | None = None


def find_scene(scene_id: str) -> SceneBundle | None:
    global _sessions
    if _sessions is None:
        _sessions = sessionmaker(bind=create_engine(get_database_url(), pool_pre_ping=True))
    with _sessions() as db:
        return load_scene(db, scene_id)


def command_sink(call_id: str, uuid: str) -> SendCommand:
    global _redis
    if _redis is None:
        _redis = redis_async.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    client = _redis

    async def send(kind: str, extra: dict) -> None:
        payload = {"type": kind, "call_id": call_id, "uuid": uuid, **extra}
        logger.info("ai command %s", payload)
        await client.lpush(AI_COMMANDS, json.dumps(payload))

    return send


def stream_audio_message(wav: bytes) -> str:
    data = {"audioDataType": "wav", "sampleRate": 8000, "audioData": base64.b64encode(wav).decode("ascii")}
    return json.dumps({"type": "streamAudio", "data": data})


@app.websocket("/fs/stream")
async def fs_stream(ws: WebSocket) -> None:
    await ws.accept()
    meta = json.loads(await ws.receive_text())
    call_id = str(meta.get("call_id") or "")
    uuid = str(meta.get("uuid") or "")
    scene_id = str(meta.get("scene_id") or "")
    send_command = command_sink(call_id, uuid)

    bundle = await asyncio.to_thread(find_scene, scene_id)
    if bundle is None:
        logger.warning("scene not found call_id=%s scene_id=%s", call_id, scene_id or "-")
        await send_command("hangup", {})
        await ws.close()
        return
    try:
        asr, tts, llm = build_asr(bundle.asr), build_tts(bundle.tts), build_llm(bundle.llm)
    except ProviderError as exc:
        logger.error("scene provider config invalid call_id=%s scene_id=%s: %s", call_id, scene_id, exc)
        kind, extra = command_for(bundle.scene, fallback_action(bundle.scene))
        await send_command(kind, extra)
        await ws.close()
        return

    async def send_audio(wav: bytes) -> None:
        await ws.send_text(stream_audio_message(wav))

    session = BotSession(
        scene=bundle.scene, asr=asr, tts=tts, llm=llm, send_audio=send_audio, send_command=send_command
    )
    logger.info("bot session start call_id=%s uuid=%s scene_id=%s", call_id, uuid, scene_id)
    try:
        await session.start()
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.on_audio(message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        logger.info("bot session end call_id=%s", call_id)
```

- [ ] **Step 6: 删除占位入口**

Run: `git rm apps/ai_gw/src/vr_ai/main.py`
Expected: `rm 'apps/ai_gw/src/vr_ai/main.py'`

- [ ] **Step 7: 运行确认通过**

Run: `python -m pytest tests/test_ai_server.py tests/test_ai_session.py -q`
Expected: PASS

- [ ] **Step 8: 部署配置**

`deploy/docker-entrypoint.sh` 中把

```sh
  ai_gw)
    echo "ai_gw runtime is not implemented yet (NotImplementedError)." >&2
    exit 1
    ;;
```

替换为：

```sh
  ai_gw)
    exec uvicorn vr_ai.server:app --host 0.0.0.0 --port 8040 "$@"
    ;;
```

`docker-compose.yml` 中把整个 `ai_gw` 服务（含上方注释行 `# 可选：机器人网关...`）替换为：

```yaml
  ai_gw:
    build:
      context: .
      dockerfile: Dockerfile
    command: [ "ai_gw" ]
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+psycopg2://vr:vr@postgres:5432/vr
      REDIS_URL: redis://redis:6379/0
    # FreeSWITCH 与 docker 同机，只对本机开放
    ports:
      - "127.0.0.1:8040:8040"
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
```

`.env.example` 末尾追加：

```
# 机器人：FreeSWITCH 推流到 ai_gw 的地址（FS 与 docker 同机）
AI_GW_WS_URL=ws://127.0.0.1:8040/fs/stream
# 机器人各厂商密钥，AI 场景里只填变量名
# DEEPSEEK_API_KEY=
# SILICONFLOW_API_KEY=
```

Run: `docker compose config --services`
Expected: 输出中包含 `ai_gw`（不再需要 `--profile robot`）

---

### Task 7: engine 机器人入口、放音事件、AI 指令

**Files:**
- Modify: `packages/common/src/vr_common/inbound.py`（整文件替换）
- Modify: `apps/engine/src/vr_engine/main.py`
- Modify: `tests/test_inbound.py`（整文件替换）、`tests/test_engine_ctx.py`、`tests/test_closed_loop.py`
- Test: `tests/test_engine_robot.py`

**Interfaces:**
- Consumes: Task 6 Redis 消息格式（由 Task 8 转成事件）。
- Produces: `inbound.robot_stream_commands(uuid: str, call_id: str, scene_id: str) -> list[FsCommand]`；`inbound.decide_park(direction: str, entry: EntryView | None, number_status: str, uuid: str) -> list[FsCommand]`（只负责拒接；有效入口返回 `[]`）。
- Produces: `EngineCtx.pending_active: dict[str, list[FsCommand]]`（取代 `pending_playback`）。
- Produces: 内部事件 `{"Event-Name": "VR_AI_CMD", "Unique-ID": uuid, "variable_vr_call_id": call_id, "type": "break"|"transfer"|"hangup", "skill_group_id": int | None}`。

- [ ] **Step 1: 写失败测试** `tests/test_engine_robot.py`

```python
import json

from vr_common.commands import FsCommand
from vr_common.registration import MemoryRegStore, mark_registered, set_reg_store
from vr_engine.main import EngineCtx, handle_event


def _event(name: str, **fields) -> dict:
    return {"Event-Name": name, "Unique-ID": "u1", "variable_vr_call_id": "c1", **fields}


def test_robot_streams_only_after_channel_active(monkeypatch):
    monkeypatch.setenv("AI_GW_WS_URL", "ws://127.0.0.1:8040/fs/stream")
    ctx = EngineCtx(entries={}, numbers={"400": "enabled"})
    parked = handle_event(
        _event(
            "CHANNEL_PARK",
            **{"Caller-Direction": "inbound", "Caller-Destination-Number": "400", "variable_vr_entry": "robot:5"},
        ),
        ctx,
    )
    assert [cmd.api for cmd in parked] == ["uuid_answer"]
    assert handle_event(_event("CHANNEL_CALLSTATE", **{"Channel-Call-State": "EARLY"}), ctx) == []

    active = handle_event(_event("CHANNEL_CALLSTATE", **{"Channel-Call-State": "ACTIVE"}), ctx)
    assert [cmd.api for cmd in active] == ["uuid_setvar", "uuid_record", "uuid_audio_stream"]
    stream_args = active[-1].args
    assert stream_args.startswith("u1 start ws://127.0.0.1:8040/fs/stream mono 8k ")
    assert json.loads(stream_args.split(" ", 5)[5]) == {"call_id": "c1", "uuid": "u1", "scene_id": "5"}


def test_stream_play_event_broadcasts_the_written_file():
    ctx = EngineCtx(entries={}, numbers={})
    cmds = handle_event(
        _event(
            "CUSTOM",
            **{
                "Event-Subclass": "mod_audio_stream::play",
                "_body": json.dumps({"audioDataType": "wav", "file": "/tmp/u1_1.wav"}),
            },
        ),
        ctx,
    )
    assert cmds == [FsCommand("uuid_broadcast", "u1 /tmp/u1_1.wav aleg")]


def test_ai_break_and_hangup_commands():
    ctx = EngineCtx(entries={}, numbers={})
    assert handle_event(_event("VR_AI_CMD", type="break"), ctx) == [FsCommand("uuid_break", "u1 all")]
    assert handle_event(_event("VR_AI_CMD", type="hangup"), ctx) == [FsCommand("uuid_kill", "u1")]


def test_ai_transfer_stops_stream_and_bridges_online_member():
    set_reg_store(MemoryRegStore())
    mark_registered("1002")
    ctx = EngineCtx(entries={}, numbers={}, skill_groups={"7": ["1001", "1002"]})
    cmds = handle_event(_event("VR_AI_CMD", type="transfer", skill_group_id=7), ctx)
    assert cmds[0] == FsCommand("uuid_audio_stream", "u1 stop")
    assert cmds[1].api == "uuid_transfer"
    assert "sofia_contact(1002@" in cmds[1].args
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_engine_robot.py -q`
Expected: FAIL（`apis == ['uuid_answer', 'uuid_setvar', ...]` 与期望不符等）

- [ ] **Step 3: inbound 改写** — `packages/common/src/vr_common/inbound.py` 整文件替换为：

```python
import json
import os
from dataclasses import dataclass

from vr_common.commands import FsCommand

_BUSY_PROMPT = "ivr/vr_busy.wav"
_RECORDING_PATH = "/var/lib/vr/recordings/${strftime(%Y%m%d)}/{call_id}.wav"
_DEFAULT_AI_GW_WS_URL = "ws://127.0.0.1:8040/fs/stream"


@dataclass(frozen=True, slots=True)
class EntryView:
    kind: str
    target: str


def _reject_call(uuid: str) -> list[FsCommand]:
    return [
        FsCommand("uuid_broadcast", f"{uuid} {_BUSY_PROMPT} aleg"),
        FsCommand("uuid_kill", uuid),
    ]


def robot_stream_commands(uuid: str, call_id: str, scene_id: str) -> list[FsCommand]:
    record_path = _RECORDING_PATH.replace("{call_id}", call_id)
    url = os.environ.get("AI_GW_WS_URL", _DEFAULT_AI_GW_WS_URL)
    metadata = json.dumps({"call_id": call_id, "uuid": uuid, "scene_id": scene_id}, separators=(",", ":"))
    return [
        FsCommand("uuid_setvar", f"{uuid} RECORD_STEREO=true"),
        FsCommand("uuid_record", f"{uuid} start {record_path}"),
        FsCommand("uuid_audio_stream", f"{uuid} start {url} mono 8k {metadata}"),
    ]


def decide_park(direction: str, entry: EntryView | None, number_status: str, uuid: str) -> list[FsCommand]:
    if direction != "inbound":
        return []
    if entry is None or number_status == "disabled":
        return _reject_call(uuid)
    return []
```

`tests/test_inbound.py` 整文件替换为：

```python
from vr_common.inbound import EntryView, decide_park, robot_stream_commands


def test_missing_entry_plays_then_hangs_up():
    cmds = decide_park("inbound", None, "active", uuid="u1")
    assert cmds[0].api == "uuid_broadcast"
    assert cmds[-1].api == "uuid_kill"
    assert "u1" in cmds[-1].args


def test_disabled_number_same_as_missing_entry():
    cmds = decide_park("inbound", EntryView(kind="robot", target="5"), "disabled", uuid="u1")
    assert cmds[0].api == "uuid_broadcast"
    assert cmds[-1].api == "uuid_kill"


def test_valid_entry_is_left_to_engine():
    assert decide_park("inbound", EntryView(kind="robot", target="5"), "active", uuid="u1") == []


def test_non_inbound_returns_empty():
    assert decide_park("outbound", None, "active", uuid="u1") == []


def test_robot_stream_records_then_streams_with_scene(monkeypatch):
    monkeypatch.setenv("AI_GW_WS_URL", "ws://gw/fs/stream")
    cmds = robot_stream_commands("u1", "c1", "5")
    assert [cmd.api for cmd in cmds] == ["uuid_setvar", "uuid_record", "uuid_audio_stream"]
    assert "RECORD_STEREO=true" in cmds[0].args
    assert "c1.wav" in cmds[1].args
    assert cmds[2].args.startswith("u1 start ws://gw/fs/stream mono 8k ")
    assert '"scene_id":"5"' in cmds[2].args
```

- [ ] **Step 4: engine 改动** — `apps/engine/src/vr_engine/main.py`

4a. import 区：

```python
from vr_common.inbound import EntryView, decide_park
```

改为：

```python
from vr_common.inbound import EntryView, decide_park, robot_stream_commands
```

```python
from vr_common.protocol import commands_for_cancel
```

改为：

```python
from vr_common.protocol import commands_for_cancel, commands_for_transfer
```

4b. `EngineCtx` 中 `pending_playback: dict[str, str] = field(default_factory=dict)` 改为：

```python
    pending_active: dict[str, list[FsCommand]] = field(default_factory=dict)
```

4c. 把 `_flush_pending_playback` 整个函数替换为：

```python
def _flush_pending_active(ctx: EngineCtx, call_id: str) -> list[FsCommand]:
    cmds = ctx.pending_active.pop(call_id, [])
    if cmds:
        logger.info("channel active call_id=%s cmds=%s", call_id, " | ".join(f"{c.api} {c.args}" for c in cmds))
    return cmds


def _stream_play_commands(event: dict[str, Any], uuid: str) -> list[FsCommand]:
    try:
        body = json.loads(event.get("_body") or "{}")
    except json.JSONDecodeError:
        return []
    path = str(body.get("file") or "") if isinstance(body, dict) else ""
    if not path or not uuid:
        return []
    return [_broadcast(uuid, path)]


def _ai_command(ctx: EngineCtx, event: dict[str, Any], uuid: str) -> list[FsCommand]:
    kind = event.get("type")
    if not uuid:
        return []
    if kind == "break":
        return commands_for_cancel(uuid)
    if kind == "hangup":
        return [FsCommand("uuid_kill", uuid)]
    if kind == "transfer":
        return commands_for_transfer(uuid) + _bridge_extension_or_queue(ctx, uuid, "", event.get("skill_group_id"))
    return []
```

4d. `_bridge_extension_or_queue` 中：

```python
        return decide_park(None, "inbound", None, "disabled", uuid, "")
```

改为：

```python
        return decide_park("inbound", None, "disabled", uuid)
```

4e. `_ivr_follow` 中 robot 分支：

```python
        elif kind == "robot" and target:
            cmds.extend(
                decide_park(
                    None,
                    "inbound",
                    EntryView(kind="robot", target=str(target)),
                    "enabled",
                    uuid,
                    call_id,
                )
            )
```

改为（IVR 按键时通话已 ACTIVE，直接推流）：

```python
        elif kind == "robot" and target:
            cmds.extend(robot_stream_commands(uuid, call_id, str(target)))
```

4f. `_enter_ivr` 中：

```python
        return decide_park(None, "inbound", None, "disabled", uuid, call_id)
```

改为：

```python
        return decide_park("inbound", None, "disabled", uuid)
```

同函数中：

```python
    if prompt:
        ctx.pending_playback[call_id] = prompt
```

改为：

```python
    if prompt:
        ctx.pending_active[call_id] = [_broadcast(uuid, prompt)]
```

4g. `_commands_for_entry` 中 playback 分支：

```python
    if entry.kind == "playback":
        ctx.pending_playback[call_id] = entry.target
        return [FsCommand("uuid_answer", uuid)]
    return []
```

改为：

```python
    if entry.kind == "playback":
        ctx.pending_active[call_id] = [_broadcast(uuid, entry.target)]
        return [FsCommand("uuid_answer", uuid)]
    if entry.kind == "robot":
        ctx.pending_active[call_id] = robot_stream_commands(uuid, call_id, entry.target)
        return [FsCommand("uuid_answer", uuid)]
    return []
```

4h. `handle_event` 中 CHANNEL_PARK 分支内：

```python
            cmds = decide_park(
                None,
                direction,
                entry,
                number_status,
                uuid,
                call_id,
            )
```

改为：

```python
            cmds = decide_park(direction, entry, number_status, uuid)
```

4i. `handle_event` 中，在 `elif event_name == "CHANNEL_PARK":` 之前插入：

```python
    elif event_name == "CUSTOM" and subclass == "mod_audio_stream::play":
        cmds = _stream_play_commands(event, uuid)
```

CHANNEL_CALLSTATE 分支：

```python
        if event.get("Channel-Call-State") == "ACTIVE":
            cmds = _flush_pending_playback(ctx, call_id, uuid)
```

改为：

```python
        if event.get("Channel-Call-State") == "ACTIVE":
            cmds = _flush_pending_active(ctx, call_id)
```

在 `elif event_name == "VR_CANCEL":` 分支之后插入：

```python
    elif event_name == "VR_AI_CMD":
        cmds = _ai_command(ctx, event, uuid)
```

4j. 话单排除集合里 `"CHANNEL_CALLSTATE",` 之后追加一行 `"VR_AI_CMD",`；挂机清理里 `ctx.pending_playback.pop(call_id, None)` 改为 `ctx.pending_active.pop(call_id, None)`。

- [ ] **Step 5: 更新已有测试**

`tests/test_engine_ctx.py` 中 `test_park_with_variable_vr_entry_routes_when_ctx_entries_empty` 末尾：

```python
    apis = [c.api for c in cmds]
    assert "uuid_kill" not in apis
    assert apis[0] == "uuid_answer"
    assert "uuid_audio_stream" in apis
```

改为：

```python
    assert [c.api for c in cmds] == ["uuid_answer"]
    assert [c.api for c in ctx.pending_active["c1"]] == ["uuid_setvar", "uuid_record", "uuid_audio_stream"]
```

`tests/test_closed_loop.py`：import 区追加 `from vr_common.commands import FsCommand`；两处

```python
    assert ctx.pending_playback["c1"] == "ivr/welcome.wav"
```

改为：

```python
    assert ctx.pending_active["c1"] == [FsCommand("uuid_broadcast", "u1 ivr/welcome.wav aleg")]
```

以及

```python
    assert "c1" not in ctx.pending_playback
```

改为：

```python
    assert "c1" not in ctx.pending_active
```

- [ ] **Step 6: 运行确认通过**

Run: `python -m pytest tests/test_engine_robot.py tests/test_inbound.py tests/test_engine_ctx.py tests/test_closed_loop.py tests/test_verify_call.py -q`
Expected: PASS

Run: `python -m pytest tests -q -k "not test_login_session_reports_bound_extension_status"`
Expected: PASS（该用例依赖本机 Redis，与本任务无关）

---

### Task 8: engine 读取 Redis 指令

**Files:**
- Modify: `apps/engine/src/vr_engine/reader.py`
- Test: `tests/test_engine_ai_commands.py`

**Interfaces:**
- Consumes: Task 6 `AI_COMMANDS` 与消息格式；Task 7 `VR_AI_CMD` 事件。
- Produces: `reader.ai_command_event(raw: str) -> dict | None`、`reader.handle_ai_command(ctx, transport, raw: str) -> None`、`reader.ai_command_loop(ctx, transport, client) -> None`；模块级锁 `_ENGINE_LOCK` 保护 `handle_event` 与 ESL 写入。

- [ ] **Step 1: 写失败测试** `tests/test_engine_ai_commands.py`

```python
import json

from vr_engine.main import EngineCtx
from vr_engine.reader import ai_command_event, handle_ai_command


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data.decode("utf-8"))


def test_ai_command_event_maps_payload():
    event = ai_command_event(
        json.dumps({"type": "transfer", "call_id": "c1", "uuid": "u1", "skill_group_id": 7})
    )
    assert event == {
        "Event-Name": "VR_AI_CMD",
        "Unique-ID": "u1",
        "variable_vr_call_id": "c1",
        "type": "transfer",
        "skill_group_id": 7,
    }


def test_bad_payload_is_ignored():
    assert ai_command_event("not json") is None
    assert ai_command_event("[1, 2]") is None


def test_handle_ai_command_sends_bgapi():
    transport = FakeTransport()
    handle_ai_command(EngineCtx(entries={}, numbers={}), transport, json.dumps({"type": "hangup", "call_id": "c1", "uuid": "u1"}))
    assert transport.sent == ["bgapi uuid_kill u1\n\n"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_engine_ai_commands.py -q`
Expected: FAIL（`ImportError: cannot import name 'ai_command_event'`）

- [ ] **Step 3: 实现** — `apps/engine/src/vr_engine/reader.py`

import 区追加：

```python
import json
import threading

import redis

from vr_common.redis_keys import AI_COMMANDS
```

在 `logger = logging.getLogger("vr.engine.esl")` 之后追加：

```python
# ESL 读循环与 AI 指令线程共用：handle_event 改 ctx，写 ESL 也必须串行
_ENGINE_LOCK = threading.Lock()
```

`read_loop` 中：

```python
    for cmd in run_maintenance(ctx):
        line = f"bgapi {cmd.api} {cmd.args}".strip()
        transport.sendall((line + "\n\n").encode("utf-8"))
```

改为：

```python
    with _ENGINE_LOCK:
        _send_commands(transport, run_maintenance(ctx))
```

`_dispatch_event` 末尾：

```python
    cmds = handle_event(event, ctx)
    for cmd in cmds:
        line = f"bgapi {cmd.api} {cmd.args}".strip()
        logger.info("esl send %s", line)
        transport.sendall((line + "\n\n").encode("utf-8"))
```

改为：

```python
    with _ENGINE_LOCK:
        _send_commands(transport, handle_event(event, ctx))


def _send_commands(transport: Any, cmds: list[Any]) -> None:
    for cmd in cmds:
        line = f"bgapi {cmd.api} {cmd.args}".strip()
        logger.info("esl send %s", line)
        transport.sendall((line + "\n\n").encode("utf-8"))


def ai_command_event(raw: str) -> dict[str, Any] | None:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return {
        "Event-Name": "VR_AI_CMD",
        "Unique-ID": str(data.get("uuid") or ""),
        "variable_vr_call_id": str(data.get("call_id") or ""),
        "type": str(data.get("type") or ""),
        "skill_group_id": data.get("skill_group_id"),
    }


def handle_ai_command(ctx: EngineCtx, transport: Any, raw: str) -> None:
    event = ai_command_event(raw)
    if event is None:
        logger.warning("ai command ignored raw=%s", raw[:200])
        return
    logger.info("ai command type=%s uuid=%s", event["type"], event["Unique-ID"] or "-")
    with _ENGINE_LOCK:
        _send_commands(transport, handle_event(event, ctx))


def ai_command_loop(ctx: EngineCtx, transport: Any, client: Any) -> None:
    while True:
        try:
            item = client.brpop(AI_COMMANDS, timeout=5)
        except Exception:
            logger.exception("ai command poll failed")
            time.sleep(1)
            continue
        if item is not None:
            handle_ai_command(ctx, transport, item[1])
```

`main()` 中：

```python
    ctx.transport = sock
    read_loop(ctx)
```

改为：

```python
    ctx.transport = sock
    client = redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    threading.Thread(target=ai_command_loop, args=(ctx, sock, client), daemon=True, name="ai-commands").start()
    read_loop(ctx)
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_engine_ai_commands.py tests/test_engine_loop.py tests/test_esl_connect.py -q`
Expected: PASS

---

### Task 9: 管理台 AI 场景页与号码入口选场景

**Files:**
- Modify: `web/src/pages/ResourcePages.tsx`（`AiScenesPage` 整体替换；`entryLabel`、`GatewaysNumbersPage` 机器人入口）
- Modify: `web/src/pages/ResourcePages.test.tsx`
- Test: `web/src/pages/ResourcePages.test.tsx`

**Interfaces:**
- Consumes: Task 1 接口与字段。
- Produces: 号码入口 `robot` 的 `entry_target` 为场景 id 字符串（Task 7 的 `scene_id`）。

- [ ] **Step 1: 写失败测试** — `web/src/pages/ResourcePages.test.tsx` 第一行改为：

```tsx
import { fireEvent, render, screen } from "@testing-library/react";
```

文件末尾追加：

```tsx
test("ai scenes page opens a form with recognition, synthesis and llm sections", async () => {
  render(<AiScenesPage />);
  fireEvent.click(screen.getByRole("button", { name: "新增场景" }));
  expect(await screen.findByText("语音识别")).toBeInTheDocument();
  expect(screen.getByText("语音合成")).toBeInTheDocument();
  expect(screen.getByText("大模型")).toBeInTheDocument();
  expect(screen.getAllByText("密钥环境变量名").length).toBe(3);
});
```

- [ ] **Step 2: 运行确认失败**

Run（在 `web/`）: `npx vitest --run src/pages/ResourcePages.test.tsx`
Expected: FAIL（找不到「新增场景」按钮）

- [ ] **Step 3: AI 场景页** — 把 `export function AiScenesPage() { ... }` 整个函数替换为：

```tsx
type ProviderConfig = {
  vendor?: string;
  base_url?: string;
  api_key_env?: string;
  model?: string;
  language?: string;
  voice?: string;
  sample_rate?: number;
};

type AiSceneRow = {
  id: number;
  name: string;
  opening: string;
  fallback: string;
  system_prompt: string;
  transfer_skill_group_id: number | null;
  asr: ProviderConfig;
  tts: ProviderConfig;
  llm: ProviderConfig;
};

type AiSceneForm = Omit<AiSceneRow, "id" | "transfer_skill_group_id"> & {
  transfer_skill_group_id?: number;
};

const FALLBACK_OPTIONS = [
  { value: "transfer", label: "转人工" },
  { value: "hangup", label: "挂机" },
];

function providerBody(config: ProviderConfig = {}): ProviderConfig {
  const text = (value?: string) => value?.trim() || undefined;
  return {
    vendor: "openai",
    base_url: text(config.base_url),
    api_key_env: text(config.api_key_env),
    model: text(config.model),
    language: text(config.language),
    voice: text(config.voice),
    sample_rate: config.sample_rate ? Number(config.sample_rate) : undefined,
  };
}

function ProviderFields({
  name,
  title,
  children,
}: {
  name: "asr" | "tts" | "llm";
  title: string;
  children?: ReactNode;
}) {
  return (
    <>
      <Typography.Title level={5}>{title}</Typography.Title>
      <Form.Item
        name={[name, "base_url"]}
        label="接口地址"
        rules={[{ required: true, message: "请填写接口地址" }]}
      >
        <Input placeholder="https://api.example.com/v1" />
      </Form.Item>
      <Form.Item name={[name, "api_key_env"]} label="密钥环境变量名" extra="密钥写在 .env，这里只填变量名">
        <Input placeholder="DEEPSEEK_API_KEY" />
      </Form.Item>
      <Form.Item name={[name, "model"]} label="模型" rules={[{ required: true, message: "请填写模型" }]}>
        <Input />
      </Form.Item>
      {children}
    </>
  );
}

export function AiScenesPage() {
  const [items, setItems] = useState<AiSceneRow[]>([]);
  const [skillGroups, setSkillGroups] = useState<{ id: number; name: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<AiSceneRow | null>(null);
  const [form] = Form.useForm<AiSceneForm>();

  const load = useCallback(async () => {
    if (!getToken()) {
      setItems([]);
      return;
    }
    setLoading(true);
    try {
      const [sceneResp, sgResp] = await Promise.all([api("/ai-scenes"), api("/skill-groups")]);
      if (sceneResp.ok) {
        const body = (await sceneResp.json()) as { items: AiSceneRow[] };
        setItems(body.items ?? []);
      }
      if (sgResp.ok) {
        const body = (await sgResp.json()) as { items: { id: number; name: string }[] };
        setSkillGroups(body.items ?? []);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function openCreate() {
    setEditing(null);
    form.setFieldsValue({
      name: "",
      opening: "",
      fallback: "transfer",
      system_prompt: "",
      transfer_skill_group_id: undefined,
      asr: { language: "zh" },
      tts: { sample_rate: 24000 },
      llm: {},
    });
    setOpen(true);
  }

  function openEdit(row: AiSceneRow) {
    setEditing(row);
    form.setFieldsValue({ ...row, transfer_skill_group_id: row.transfer_skill_group_id ?? undefined });
    setOpen(true);
  }

  async function onSubmit(values: AiSceneForm) {
    const body = {
      name: values.name.trim(),
      opening: values.opening?.trim() ?? "",
      fallback: values.fallback,
      system_prompt: values.system_prompt?.trim() ?? "",
      transfer_skill_group_id: values.transfer_skill_group_id ?? null,
      asr: providerBody(values.asr),
      tts: providerBody(values.tts),
      llm: providerBody(values.llm),
    };
    const resp = editing
      ? await api(`/ai-scenes/${editing.id}`, { method: "PUT", body: JSON.stringify(body) })
      : await api("/ai-scenes", { method: "POST", body: JSON.stringify(body) });
    if (!resp.ok) {
      message.error(editing ? "保存失败" : "新增失败");
      return;
    }
    message.success(editing ? "已保存" : "已新增");
    setOpen(false);
    await load();
  }

  async function onDelete(row: AiSceneRow) {
    const resp = await api(`/ai-scenes/${row.id}`, { method: "DELETE" });
    if (!resp.ok) {
      message.error("删除失败");
      return;
    }
    message.success("已删除");
    await load();
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          AI 场景
        </Typography.Title>
        <Button type="primary" onClick={openCreate}>
          新增场景
        </Button>
      </div>
      <Table<AiSceneRow>
        style={{ marginTop: 12 }}
        size="small"
        bordered
        rowKey="id"
        loading={loading}
        dataSource={items}
        locale={{ emptyText: getToken() ? "暂无数据" : "请先在右上角登录" }}
        pagination={false}
        columns={[
          { title: "ID", dataIndex: "id", width: 60 },
          { title: "名称", dataIndex: "name" },
          { title: "开场白", dataIndex: "opening", ellipsis: true },
          { title: "对话模型", render: (_: unknown, row: AiSceneRow) => row.llm?.model || "—" },
          {
            title: "降级",
            dataIndex: "fallback",
            render: (value: string) => FALLBACK_OPTIONS.find((item) => item.value === value)?.label ?? value,
          },
          {
            title: "转人工技能组",
            dataIndex: "transfer_skill_group_id",
            render: (value: number | null) => skillGroups.find((sg) => sg.id === value)?.name ?? "—",
          },
          {
            title: "操作",
            width: 140,
            render: (_: unknown, row: AiSceneRow) => (
              <Space>
                <Button type="link" size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm title="确认删除该场景？" onConfirm={() => onDelete(row)}>
                  <Button type="link" size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal
        title={editing ? "编辑场景" : "新增场景"}
        open={open}
        width={720}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={(values) => void onSubmit(values)}>
          <Form.Item name="name" label="名称" rules={[{ required: true, message: "请输入名称" }]}>
            <Input placeholder="售后机器人" />
          </Form.Item>
          <Form.Item name="opening" label="开场白" extra="不含「智能助理」时会自动在前面加「我是智能助理。」">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="system_prompt" label="系统提示词">
            <Input.TextArea rows={4} placeholder="你是某某公司的电话客服，回答简短口语化" />
          </Form.Item>
          <Form.Item name="fallback" label="失败降级" rules={[{ required: true, message: "请选择降级方式" }]}>
            <Select options={FALLBACK_OPTIONS} />
          </Form.Item>
          <Form.Item name="transfer_skill_group_id" label="转人工技能组">
            <Select
              allowClear
              placeholder="选择技能组"
              options={skillGroups.map((sg) => ({ value: sg.id, label: sg.name }))}
            />
          </Form.Item>
          <ProviderFields name="asr" title="语音识别">
            <Form.Item name={["asr", "language"]} label="语言">
              <Input placeholder="zh" />
            </Form.Item>
          </ProviderFields>
          <ProviderFields name="tts" title="语音合成">
            <Form.Item name={["tts", "voice"]} label="音色">
              <Input placeholder="alloy" />
            </Form.Item>
            <Form.Item name={["tts", "sample_rate"]} label="输出采样率">
              <Input type="number" placeholder="24000" />
            </Form.Item>
          </ProviderFields>
          <ProviderFields name="llm" title="大模型" />
        </Form>
      </Modal>
    </div>
  );
}
```

- [ ] **Step 4: 号码入口选场景** — 同文件 `GatewaysNumbersPage` 与 `entryLabel`：

4a. `entryLabel` 的 `lookups` 类型中 `ivrFlows: EntryOptionRow[];` 后追加 `aiScenes: EntryOptionRow[];`；`else if (row.entry_kind === "ivr") { ... }` 之后追加：

```tsx
  } else if (row.entry_kind === "robot") {
    const scene = lookups.aiScenes.find((item) => String(item.id) === row.entry_target);
    if (scene?.name) {
      target = scene.name;
    }
```

4b. `const [ivrFlows, setIvrFlows] = useState<EntryOptionRow[]>([]);` 后追加：

```tsx
  const [aiScenes, setAiScenes] = useState<EntryOptionRow[]>([]);
```

`load` 中未登录分支 `setIvrFlows([]);` 后追加 `setAiScenes([]);`；请求改为：

```tsx
      const [numberResp, gatewayResp, extResp, sgResp, ivrResp, sceneResp] = await Promise.all([
        api("/numbers"),
        api("/gateways"),
        api("/extensions"),
        api("/skill-groups"),
        api("/ivr"),
        api("/ai-scenes"),
      ]);
```

在 `if (ivrResp.ok) { ... }` 之后追加：

```tsx
      if (sceneResp.ok) {
        const body = (await sceneResp.json()) as { items: EntryOptionRow[] };
        setAiScenes(body.items ?? []);
      }
```

4c. `entryLabel(row, { extensions, skillGroups, ivrFlows })` 改为 `entryLabel(row, { extensions, skillGroups, ivrFlows, aiScenes })`。

4d. 入口目标渲染函数最后的默认分支：

```tsx
              return (
                <Form.Item
                  name="entry_target"
                  label="入口目标"
                  rules={[{ required: true, message: "请填写目标" }]}
                  extra="机器人场景后续接入，当前先手填目标标识"
                >
                  <Input placeholder="机器人场景标识" />
                </Form.Item>
              );
```

替换为：

```tsx
              return (
                <Form.Item
                  name="entry_target"
                  label="入口目标"
                  rules={[{ required: true, message: "请选择 AI 场景" }]}
                >
                  <Select
                    placeholder="选择 AI 场景"
                    options={aiScenes.map((scene) => ({
                      value: String(scene.id),
                      label: `${scene.id} ${scene.name}`,
                    }))}
                  />
                </Form.Item>
              );
```

- [ ] **Step 5: 运行确认通过**

Run（在 `web/`）: `npx vitest --run`
Expected: PASS（全部用例）

Run（在 `web/`）: `npm run build`
Expected: `✓ built in ...`，无报错

---

### Task 10: 端到端联调（部署后人工验证）

**Files:** 无代码改动

- [ ] **Step 1: 部署**

```bash
docker compose up -d --build migrate api engine ai_gw web
docker compose logs -f ai_gw engine
```

Expected: `ai_gw` 日志出现 `Uvicorn running on http://0.0.0.0:8040`；`engine` 正常连上 ESL。

- [ ] **Step 2: 配置** — `.env` 写入大模型 / 识别 / 合成的密钥（如 `DEEPSEEK_API_KEY=...`），重启 `ai_gw`；管理台新增 AI 场景并选转人工技能组；号码入口选「机器人」并选该场景。

- [ ] **Step 3: 拨打验证**（对应设计文档第 10 节核对项）

1. 听到开场白；`engine` 日志依次出现 `channel active ... uuid_audio_stream start`、`uuid_broadcast <uuid> /tmp/...`。若放音事件里的文件路径字段不是 `file`，以 FS 日志 `mod_audio_stream::play` 的正文为准修改 `_stream_play_commands`。
2. 问一个问题，机器人按句回复；连续两句之间不互相打断。若后一句会打断前一句，在 `BotSession._speak` 发送前按 `_speaking_until` 等待上一句播完。
3. 机器人说话时插话，播报立即停止（`ai command type=break`）。
4. 说「转人工」，话术播完后转到技能组在线分机。
5. 停掉大模型密钥或填错模型，呼入后按场景降级。
