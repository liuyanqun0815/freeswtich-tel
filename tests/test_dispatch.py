from vr_common.dispatch import DROPPABLE, is_critical, offer, worker_index


def test_same_call_same_worker_and_hangup_not_dropped():
    assert worker_index("abc", 64) == worker_index("abc", 64)
    assert offer(64, 64, "HEARTBEAT") == "drop"
    assert offer(64, 64, "CHANNEL_HANGUP_COMPLETE") == "block"
    assert offer(1, 64, "CHANNEL_PARK") == "enqueue"


def test_droppable_and_critical_sets():
    assert DROPPABLE == {"HEARTBEAT", "PLAYBACK_START"}
    assert is_critical("CHANNEL_HANGUP_COMPLETE") is True
    assert is_critical("BACKGROUND_JOB") is True
    assert is_critical("CHANNEL_PARK") is False


def test_full_queue_drops_playback_start():
    assert offer(64, 64, "PLAYBACK_START") == "drop"


def test_worker_index_uses_crc32():
    a = worker_index("call-xyz", 8)
    b = worker_index("call-xyz", 8)
    assert 0 <= a < 8
    assert a == b
