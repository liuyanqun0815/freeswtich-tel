import zlib

DROPPABLE = frozenset({"HEARTBEAT", "PLAYBACK_START"})

_CRITICAL = frozenset({"CHANNEL_HANGUP_COMPLETE", "BACKGROUND_JOB"})


def worker_index(call_id: str, n: int) -> int:
    return zlib.crc32(call_id.encode()) % n


def is_critical(event_name: str) -> bool:
    return event_name in _CRITICAL


def offer(queue_size: int, capacity: int, event_name: str) -> str:
    if queue_size < capacity:
        return "enqueue"
    if is_critical(event_name):
        return "block"
    if event_name in DROPPABLE:
        return "drop"
    return "block"
