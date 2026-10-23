import json

from fastapi.testclient import TestClient

from support import get_test_session, seed_user
from vr_api.main import app
from vr_common.db import DEFAULT_TENANT
from vr_common.esljobs import MemoryJobQueue, set_job_queue
from vr_common.inbound import EntryView
from vr_common.models import ConfigRelease
from vr_engine.main import EngineCtx, handle_event


def test_extension_save_enqueues_directory_flush():
    queue = MemoryJobQueue()
    set_job_queue(queue)
    try:
        seed_user("admin_flush", "admin-pass", "admin", must_change_password=False)
        client = TestClient(app)
        login = client.post(
            "/auth/login", json={"username": "admin_flush", "password": "admin-pass"}
        )
        token = login.json()["access_token"]
        created = client.post(
            "/extensions",
            headers={"Authorization": f"Bearer {token}"},
            json={"ext_number": "8100", "display_name": "甲", "enabled": True},
        )
        assert created.status_code == 201
        raw = queue.pop()
        assert raw is not None
        assert json.loads(raw)["cmd"] == "xml_flush_cache 8100"
    finally:
        set_job_queue(None)


def test_heartbeat_flushes_directory_and_applies_acl():
    queue = MemoryJobQueue()
    set_job_queue(queue)
    try:
        queue.push(json.dumps({"cmd": "xml_flush_cache 8100"}))
        seed_user("admin_acl_loop", "admin-pass", "admin", must_change_password=False)
        client = TestClient(app)
        login = client.post(
            "/auth/login", json={"username": "admin_acl_loop", "password": "admin-pass"}
        )
        token = login.json()["access_token"]
        created = client.post(
            "/releases",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "kind": "acl",
                "name": "vr_acl",
                "snapshot": {"acl": ["10.0.0.8"]},
                "needs_register": False,
            },
        )
        release_id = created.json()["id"]
        sent: list[str] = []

        class _Transport:
            def sendall(self, data: bytes) -> None:
                sent.append(data.decode("utf-8"))

        db = next(get_test_session())
        try:
            ctx = EngineCtx(entries={}, numbers={}, transport=_Transport(), db=db)
            handle_event({"Event-Name": "HEARTBEAT"}, ctx)
            assert any(cmd.api == "xml_flush_cache" and cmd.args == "8100" for cmd in ctx.sent)
            assert any("reloadacl" in line for line in sent)
            row = db.get(ConfigRelease, release_id)
            assert row is not None
            assert row.status == "applied"
        finally:
            db.close()
    finally:
        set_job_queue(None)


def test_gateway_release_stays_pending_until_reged():
    seed_user("admin_gw_loop", "admin-pass", "admin", must_change_password=False)
    client = TestClient(app)
    login = client.post(
        "/auth/login", json={"username": "admin_gw_loop", "password": "admin-pass"}
    )
    token = login.json()["access_token"]
    created = client.post(
        "/releases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "kind": "gateway_create",
            "name": "gw-live",
            "snapshot": {"gateways": [{"name": "gw-live"}]},
            "needs_register": True,
        },
    )
    release_id = created.json()["id"]

    class _Transport:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.body = "State FAIL_WAIT"

        def sendall(self, data: bytes) -> None:
            self.sent.append(data.decode("utf-8"))

        def recv(self, _n: int) -> bytes:
            body = self.body.encode("utf-8")
            header = f"Content-Type: api/response\nContent-Length: {len(body)}\n\n".encode()
            return header + body

    transport = _Transport()
    db = next(get_test_session())
    try:
        ctx = EngineCtx(entries={}, numbers={}, transport=transport, db=db)
        handle_event({"Event-Name": "HEARTBEAT"}, ctx)
        row = db.get(ConfigRelease, release_id)
        assert row is not None
        assert row.status == "pending"
        assert sum("sofia profile external rescan" in line for line in transport.sent) == 1

        transport.body = "State REGED"
        handle_event({"Event-Name": "HEARTBEAT"}, ctx)
        db.refresh(row)
        assert row.status == "applied"
        assert sum("sofia profile external rescan" in line for line in transport.sent) == 1
    finally:
        db.close()


def test_park_ivr_is_not_dialplan_and_dtmf_runs_snapshot():
    snapshot = {
        "id": "root",
        "kind": "menu",
        "prompt": "ivr/welcome.wav",
        "children": [{"digit": "1", "id": "n1", "kind": "hangup"}],
    }
    ctx = EngineCtx(
        entries={},
        numbers={"400": "enabled"},
        ivr_snapshots={"3": (2, snapshot)},
    )
    parked = handle_event(
        {
            "Event-Name": "CHANNEL_PARK",
            "Unique-ID": "u1",
            "variable_vr_call_id": "c1",
            "Caller-Direction": "inbound",
            "Caller-Destination-Number": "400",
            "variable_vr_entry": "ivr:3",
        },
        ctx,
    )
    apis = [cmd.api for cmd in parked]
    assert "uuid_answer" in apis
    assert any("ivr/welcome.wav" in cmd.args for cmd in parked)
    assert all(cmd.api != "transfer" for cmd in parked)
    assert ctx.sessions["c1"].ivr_version == 2

    digits = handle_event(
        {
            "Event-Name": "DTMF",
            "Unique-ID": "u1",
            "variable_vr_call_id": "c1",
            "DTMF-Digit": "1",
        },
        ctx,
    )
    assert digits[-1].api == "uuid_kill"
    assert ctx.sessions["c1"].ivr_version == 2


def test_park_extension_and_queue_emit_commands():
    ctx = EngineCtx(
        entries={
            "400": EntryView(kind="extension", target="8001"),
            "401": EntryView(kind="queue", target="sales"),
        },
        numbers={"400": "enabled", "401": "enabled"},
    )
    extension = handle_event(
        {
            "Event-Name": "CHANNEL_PARK",
            "Unique-ID": "u1",
            "variable_vr_call_id": "c-ext",
            "Caller-Direction": "inbound",
            "Caller-Destination-Number": "400",
        },
        ctx,
    )
    transfer = next(cmd for cmd in extension if cmd.api == "uuid_transfer")
    assert "sofia_contact(8001@" in transfer.args
    assert any(cmd.api == "uuid_pre_answer" for cmd in extension)

    queue = handle_event(
        {
            "Event-Name": "CHANNEL_PARK",
            "Unique-ID": "u2",
            "variable_vr_call_id": "c-q",
            "Caller-Direction": "inbound",
            "Caller-Destination-Number": "401",
        },
        ctx,
    )
    assert queue[1].api == "uuid_setvar"
    assert "vr_skill_group=sales" in queue[1].args
