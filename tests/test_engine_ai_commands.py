import json

from vr_engine.main import EngineCtx
from vr_engine.reader import ai_command_event, handle_ai_command


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
