from vr_common.commands import (
    bridge,
    finish_if_no_next,
    on_leg_hangup,
    stop_stream,
)


def test_bridge_returns_other_leg_to_park():
    cmds = bridge("ua", "ub")
    assert any(
        c.api == "uuid_setvar" and "ua" in c.args and "park_after_bridge=true" in c.args
        for c in cmds
    )
    assert any(
        c.api == "uuid_setvar" and "ub" in c.args and "park_after_bridge=true" in c.args
        for c in cmds
    )
    assert cmds[-1].api == "uuid_bridge"
    assert cmds[-1].args == "ua ub"
    assert on_leg_hangup("ub", True) == []


def test_on_leg_hangup_kills_other_when_not_parked():
    cmds = on_leg_hangup("ub", False)
    assert len(cmds) == 1
    assert cmds[0].api == "uuid_kill"
    assert cmds[0].args == "ub"


def test_stop_stream():
    cmd = stop_stream("ua")
    assert cmd.api == "uuid_audio_stream"
    assert cmd.args == "ua stop"


def test_finish_if_no_next():
    cmd = finish_if_no_next("ua")
    assert cmd.api == "uuid_kill"
    assert cmd.args == "ua"
