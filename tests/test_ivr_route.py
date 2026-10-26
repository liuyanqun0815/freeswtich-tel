from vr_common.ivr_route import pick_bridge_extension


def test_pick_preferred_when_registered():
    online = {"1000", "1001"}
    assert (
        pick_bridge_extension("1000", ["1001", "1002"], registered=lambda e: e in online)
        == "1000"
    )


def test_pick_skill_member_when_preferred_offline():
    online = {"1001"}
    assert (
        pick_bridge_extension("1000", ["1002", "1001"], registered=lambda e: e in online)
        == "1001"
    )


def test_pick_none_when_nobody_online():
    assert (
        pick_bridge_extension("1000", ["1001", "1002"], registered=lambda _e: False)
        is None
    )
