from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app
from vr_common.registration import (
    MemoryRegStore,
    is_registered,
    mark_registered,
    parse_sofia_registrations,
    registration_view,
    replace_registered,
    set_reg_store,
)
from vr_engine.main import EngineCtx, handle_event


def test_unregistered_extension_cannot_dial():
    set_reg_store(MemoryRegStore())
    try:
        seed_user("admin_reg", "admin-pass", "admin", must_change_password=False)
        client = TestClient(app)
        login = client.post("/auth/login", json={"username": "admin_reg", "password": "admin-pass"})
        token = login.json()["access_token"]
        denied = client.post(
            "/calls",
            headers={"Authorization": f"Bearer {token}"},
            json={"caller": "8001", "callee": "8002"},
        )
        assert denied.status_code == 409
        assert "8001" in denied.json()["detail"]
    finally:
        set_reg_store(None)


def test_register_event_then_unregister_and_heartbeat_snapshot():
    set_reg_store(MemoryRegStore())
    try:
        handle_event(
            {
                "Event-Name": "CUSTOM",
                "Event-Subclass": "sofia::register",
                "from-user": "8001",
                "contact": "sip:8001@10.0.0.8",
            },
            EngineCtx(entries={}, numbers={}),
        )
        assert is_registered("8001") is True

        handle_event(
            {
                "Event-Name": "CUSTOM",
                "Event-Subclass": "sofia::unregister",
                "from-user": "8001",
            },
            EngineCtx(entries={}, numbers={}),
        )
        assert is_registered("8001") is False

        mark_registered("8001", "sip:8001@10.0.0.8")
        mark_registered("8009")
        body = "\n".join(
            [
                "Registrations:",
                "abc  8001@t001.vr.local  sofia/internal/sip:8001@10.0.0.8",
                "Total items returned: 1",
            ]
        )
        assert parse_sofia_registrations(body) == {"8001"}
        replace_registered(parse_sofia_registrations(body))
        assert is_registered("8001") is True
        assert registration_view("8001")["contact"] == "sip:8001@10.0.0.8"
        assert is_registered("8009") is False
    finally:
        set_reg_store(None)
