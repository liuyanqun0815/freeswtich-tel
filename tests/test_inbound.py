from vr_common.inbound import EntryView, decide_park
from vr_common.session import CallSession


def test_missing_entry_plays_then_hangs_up():
    cmds = decide_park(None, "inbound", None, "active", uuid="u1", call_id="c1")
    assert cmds[0].api == "uuid_broadcast"
    assert cmds[-1].api == "uuid_kill"
    assert "u1" in cmds[-1].args


def test_robot_answers_records_and_streams():
    entry = EntryView(kind="robot", target="scene-1")
    cmds = decide_park(None, "inbound", entry, "active", uuid="u1", call_id="c1")
    apis = [c.api for c in cmds]
    assert apis == ["uuid_answer", "uuid_setvar", "uuid_record", "uuid_audio_stream"]
    assert "RECORD_STEREO=true" in cmds[1].args
    assert "c1" in cmds[2].args


def test_disabled_number_same_as_missing_entry():
    entry = EntryView(kind="robot", target="scene-1")
    cmds = decide_park(None, "inbound", entry, "disabled", uuid="u1", call_id="c1")
    assert cmds[0].api == "uuid_broadcast"
    assert cmds[-1].api == "uuid_kill"


def test_non_inbound_returns_empty():
    entry = EntryView(kind="robot", target="scene-1")
    assert decide_park(None, "outbound", entry, "active", uuid="u1", call_id="c1") == []


def test_existing_session_robot_skips_answer():
    existing = CallSession(
        call_id="c1",
        fs_node="fs1",
        tenant_id="t1",
        entry="robot:scene-1",
        state="active",
    )
    entry = EntryView(kind="robot", target="scene-1")
    cmds = decide_park(existing, "inbound", entry, "active", uuid="u2", call_id="c1")
    apis = [c.api for c in cmds]
    assert "uuid_answer" not in apis
    assert apis == ["uuid_setvar", "uuid_record", "uuid_audio_stream"]
