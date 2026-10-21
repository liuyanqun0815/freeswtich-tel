from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from support import get_test_session, init_sqlite, seed_user
from vr_common.agent import (
    AGENT_WS_MESSAGE_TYPES,
    can_take_call,
    dial_agent_after_pop,
    on_ws_silent,
    pop_before_dial,
    state_for_agent_message,
)
from vr_common.models import AgentStateLog
from vr_common.security import create_access_token
from vr_push.main import ALLOWED_AGENT_MESSAGE_TYPES, app, get_db

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_available_requires_sip_ws_and_idle():
    assert can_take_call(True, True, "idle") is True
    assert can_take_call(True, False, "idle") is False
    assert on_ws_silent(60, "idle") == "break"
    assert on_ws_silent(59, "idle") == "idle"
    msg = pop_before_dial("要转人工", "c1")
    assert msg["type"] == "call.incoming"
    assert msg["summary"] == "要转人工"


def test_can_take_call_requires_idle_state():
    assert can_take_call(True, True, "ringing") is False
    assert can_take_call(False, True, "idle") is False


def test_pop_before_dial_includes_call_id():
    msg = pop_before_dial("summary", "call-42")
    assert msg["call_id"] == "call-42"


def test_dial_agent_after_pop_sets_up_before_originate():
    cmds = dial_agent_after_pop("1001", "c1")
    assert cmds
    assert cmds[0].api != "originate"
    originate = [c for c in cmds if c.api == "originate"]
    assert len(originate) == 1
    assert "1001" in originate[0].args
    assert "c1" in originate[0].args


def test_state_for_agent_message_maps_ready_and_not_ready():
    assert state_for_agent_message("agent.ready") == "idle"
    assert state_for_agent_message("agent.not_ready") == "break"
    assert state_for_agent_message("agent.wrapup_done") == "idle"
    assert state_for_agent_message("call.hangup") is None


def test_agent_ws_message_types_match_push_whitelist():
    assert AGENT_WS_MESSAGE_TYPES == ALLOWED_AGENT_MESSAGE_TYPES


def test_push_and_ai_gw_sources_exclude_esl_and_8021():
    roots = [
        _REPO_ROOT / "apps" / "push" / "src",
        _REPO_ROOT / "apps" / "ai_gw" / "src",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            assert "8021" not in text, path
            assert "esl" not in text, path


@pytest.fixture
def push_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    init_sqlite()
    user = seed_user("agent-ws", "secret-pass", "agent")
    app.dependency_overrides[get_db] = get_test_session
    token = create_access_token(user.id, user.role, user.tenant_id)
    client = TestClient(app)
    yield client, token, user.id
    app.dependency_overrides.clear()


def test_ws_agent_requires_auth_first(push_client):
    client, token, _user_id = push_client
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/agent") as ws:
            ws.send_json({"type": "ping"})
            ws.receive_json()

    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}


def test_ws_agent_ready_writes_agent_state_log(push_client):
    client, token, user_id = push_client
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.send_json({"type": "agent.ready"})
        ws.receive_json()

    db = next(get_test_session())
    try:
        row = db.scalars(
            select(AgentStateLog)
            .where(AgentStateLog.user_id == user_id)
            .order_by(AgentStateLog.id.desc())
        ).first()
        assert row is not None
        assert row.state == "idle"
    finally:
        db.close()


def test_ws_agent_unknown_type_is_ignored(push_client):
    client, token, user_id = push_client
    with client.websocket_connect("/ws/agent") as ws:
        ws.send_json({"type": "auth", "token": token})
        ws.send_json({"type": "agent.ready"})
        ws.receive_json()
        ws.send_json({"type": "not.a.real.type"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    db = next(get_test_session())
    try:
        rows = db.scalars(select(AgentStateLog).where(AgentStateLog.user_id == user_id)).all()
        assert len(rows) == 1
    finally:
        db.close()
