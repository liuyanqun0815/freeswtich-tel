from datetime import datetime, timedelta, timezone

from vr_common.acd import AgentSnapshot, overflow, pick_longest_idle, on_ring_timeout


def test_longest_idle_and_ring_timeout():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    agents = [
        AgentSnapshot("a", idle_since=now - timedelta(seconds=10), sip_registered=True, ws_online=True, state="idle"),
        AgentSnapshot("b", idle_since=now - timedelta(seconds=50), sip_registered=True, ws_online=True, state="idle"),
        AgentSnapshot("c", idle_since=now - timedelta(seconds=90), sip_registered=False, ws_online=True, state="idle"),
    ]
    assert pick_longest_idle(agents) == "b"
    assert on_ring_timeout("b", 20, 20) == "requeue"
    assert on_ring_timeout("b", 19, 20) == "keep"


def test_pick_longest_idle_returns_none_when_none_eligible():
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    agents = [
        AgentSnapshot("c", idle_since=now - timedelta(seconds=90), sip_registered=False, ws_online=True, state="idle"),
        AgentSnapshot("d", idle_since=now - timedelta(seconds=100), sip_registered=True, ws_online=True, state="talking"),
    ]
    assert pick_longest_idle(agents) is None
    assert pick_longest_idle([]) is None


def test_overflow_returns_target():
    assert overflow("queue_full", "queue:sg2") == "queue:sg2"
    assert overflow("no_agent", "robot:fallback") == "robot:fallback"
    assert overflow("timeout", "hangup") == "hangup"
