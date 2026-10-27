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
