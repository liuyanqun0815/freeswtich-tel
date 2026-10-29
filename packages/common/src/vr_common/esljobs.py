"""Cross-process ESL jobs. API enqueues; only the engine executes them."""

from __future__ import annotations

import json
import os
from typing import Any, Protocol

from vr_common.redis_keys import ESL_JOBS
from vr_common.release import on_extension_saved

DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


class JobQueue(Protocol):
    def push(self, payload: str) -> None: ...

    def pop(self) -> str | None: ...


class MemoryJobQueue:
    def __init__(self) -> None:
        self._items: list[str] = []

    def push(self, payload: str) -> None:
        self._items.append(payload)

    def pop(self) -> str | None:
        if not self._items:
            return None
        return self._items.pop(0)


class RedisJobQueue:
    def __init__(self, client: Any) -> None:
        self._client = client

    def push(self, payload: str) -> None:
        self._client.rpush(ESL_JOBS, payload)

    def pop(self) -> str | None:
        raw = self._client.lpop(ESL_JOBS)
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode("utf-8")


_queue: JobQueue | None = None


def set_job_queue(queue: JobQueue | None) -> None:
    global _queue
    _queue = queue


def get_job_queue() -> JobQueue:
    global _queue
    if _queue is None:
        import redis

        url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
        _queue = RedisJobQueue(redis.from_url(url, decode_responses=True))
    return _queue


def enqueue_command(cmd: str) -> None:
    get_job_queue().push(json.dumps({"cmd": cmd}))


def enqueue_blind_transfer(from_ext: str, to_ext: str) -> None:
    get_job_queue().push(
        json.dumps({"type": "blind_transfer", "from_ext": from_ext, "to_ext": to_ext}, ensure_ascii=False)
    )


def enqueue_directory_flush(ext: str) -> None:
    """Directory users are cacheable, so a save must flush that user."""
    for cmd in on_extension_saved(ext, True):
        get_job_queue().push(json.dumps({"cmd": cmd}))


def drain_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    queue = get_job_queue()
    while True:
        raw = queue.pop()
        if raw is None:
            break
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            jobs.append(payload)
    return jobs


def drain_commands() -> list[str]:
    commands: list[str] = []
    for payload in drain_jobs():
        cmd = payload.get("cmd")
        if isinstance(cmd, str) and cmd:
            commands.append(cmd)
    return commands
