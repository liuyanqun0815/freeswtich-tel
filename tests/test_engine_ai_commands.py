import json

import pytest

from vr_engine.main import EngineCtx
from vr_engine.reader import ai_command_event, ai_command_loop, handle_ai_command


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data.decode("utf-8"))


def test_ai_command_event_maps_payload():
    event = ai_command_event(json.dumps({"type": "transfer", "call_id": "c1", "uuid": "u1", "skill_group_id": 7}))
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
    handle_ai_command(
        EngineCtx(entries={}, numbers={}), transport, json.dumps({"type": "hangup", "call_id": "c1", "uuid": "u1"})
    )
    assert transport.sent == ["bgapi uuid_kill u1\n\n"]


class BrokenTransport:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.sent: list[str] = []

    def sendall(self, data: bytes) -> None:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise OSError("broken pipe")
        self.sent.append(data.decode("utf-8"))


class StopLoop(BaseException):
    pass


class FakeRedis:
    def __init__(self, items: list[tuple[str, str]]) -> None:
        self.items = list(items)

    def brpop(self, key: str, timeout: int = 0) -> tuple[str, str]:
        if not self.items:
            raise StopLoop()
        return self.items.pop(0)


def test_handle_ai_command_survives_send_error():
    transport = BrokenTransport(fail_times=1)
    handle_ai_command(
        EngineCtx(entries={}, numbers={}), transport, json.dumps({"type": "hangup", "call_id": "c1", "uuid": "u1"})
    )
    assert transport.sent == []


class FailingRollbackDb:
    def __init__(self) -> None:
        self.rollbacks = 0

    def rollback(self) -> None:
        self.rollbacks += 1
        raise RuntimeError("connection lost")


def test_handle_ai_command_survives_failing_rollback():
    db = FailingRollbackDb()
    ctx = EngineCtx(entries={}, numbers={}, db=db)
    handle_ai_command(ctx, BrokenTransport(fail_times=1), json.dumps({"type": "hangup", "call_id": "c1", "uuid": "u1"}))
    assert db.rollbacks == 1


def test_ai_command_loop_continues_after_failed_command():
    transport = BrokenTransport(fail_times=1)
    client = FakeRedis(
        [
            ("vr:ai:cmd", json.dumps({"type": "hangup", "call_id": "c1", "uuid": "u1"})),
            ("vr:ai:cmd", json.dumps({"type": "hangup", "call_id": "c2", "uuid": "u2"})),
        ]
    )
    with pytest.raises(StopLoop):
        ai_command_loop(EngineCtx(entries={}, numbers={}), transport, client)
    assert transport.sent == ["bgapi uuid_kill u2\n\n"]
