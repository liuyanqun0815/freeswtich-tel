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
