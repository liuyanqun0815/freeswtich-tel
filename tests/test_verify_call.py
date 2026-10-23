import json

from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app
from vr_common.esljobs import MemoryJobQueue, set_job_queue
from vr_common.registration import MemoryRegStore, mark_registered, set_reg_store
from vr_common.verify_call import MemoryCallStore, load_call, set_call_store
from vr_engine.main import EngineCtx, handle_event


def _admin_token() -> str:
    seed_user("admin_call", "admin-pass", "admin", must_change_password=False)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin_call", "password": "admin-pass"})
    assert login.status_code == 200
    return login.json()["access_token"]


def test_dial_enqueues_park_then_engine_transfers_and_answers():
    jobs = MemoryJobQueue()
    calls = MemoryCallStore()
    regs = MemoryRegStore()
    set_job_queue(jobs)
    set_call_store(calls)
    set_reg_store(regs)
    try:
        mark_registered("8001")
        mark_registered("8002")
        token = _admin_token()
        client = TestClient(app)
        created = client.post(
            "/calls",
            headers={"Authorization": f"Bearer {token}"},
            json={"caller": "8001", "callee": "8002"},
        )
        assert created.status_code == 201
        call_id = created.json()["call_id"]
        command = json.loads(jobs.pop())["cmd"]
        assert command.startswith("originate ")
        assert f"vr_call_id={call_id}" in command
        assert "vr_entry=extension:8002" in command
        assert command.endswith("user/8001 &park()")

        ctx = EngineCtx(entries={}, numbers={})
        parked = handle_event(
            {
                "Event-Name": "CHANNEL_PARK",
                "Unique-ID": call_id,
                "variable_vr_call_id": call_id,
                "Caller-Direction": "outbound",
                "Caller-Destination-Number": "8001",
                "variable_vr_entry": "extension:8002",
            },
            ctx,
        )
        assert parked[-1].api == "uuid_transfer"
        assert "sofia_contact(8002@" in parked[-1].args
        assert load_call(call_id)["state"] == "transferring"

        handle_event(
            {
                "Event-Name": "CHANNEL_ANSWER",
                "Unique-ID": "leg-b",
                "variable_vr_call_id": call_id,
            },
            ctx,
        )
        assert load_call(call_id)["state"] == "answered"

        hung = client.post(
            f"/calls/{call_id}/hangup",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert hung.status_code == 200
        kill = json.loads(jobs.pop())["cmd"]
        assert kill.startswith("uuid_kill ")
    finally:
        set_job_queue(None)
        set_call_store(None)
        set_reg_store(None)


def test_background_job_error_marks_call_failed():
    calls = MemoryCallStore()
    set_call_store(calls)
    try:
        from vr_common.verify_call import save_call

        save_call({"call_id": "abc-1234", "state": "queued", "uuids": [], "detail": ""})
        handle_event(
            {
                "Event-Name": "BACKGROUND_JOB",
                "Job-Command": "originate {vr_call_id=abc-1234}user/8001 &park()",
                "_body": "-ERR USER_NOT_REGISTERED",
            },
            EngineCtx(entries={}, numbers={}),
        )
        row = load_call("abc-1234")
        assert row is not None
        assert row["state"] == "failed"
        assert "USER_NOT_REGISTERED" in row["detail"]
    finally:
        set_call_store(None)
