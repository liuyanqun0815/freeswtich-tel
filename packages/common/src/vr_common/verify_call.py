"""Admin call verification: ESL originate into the park flow, state in Redis."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Protocol

from vr_common.db import DEFAULT_TENANT

_CALL_TTL = 14400
_ID_RE = re.compile(r"vr_call_id=([0-9a-fA-F-]{8,})")


class CallStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def setex(self, key: str, ttl: int, value: str) -> None: ...


class MemoryCallStore:
    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._items.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        _ = ttl
        self._items[key] = value


class RedisCallStore:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> str | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode("utf-8")

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._client.setex(key, ttl, value)


_store: CallStore | None = None


def set_call_store(store: CallStore | None) -> None:
    global _store
    _store = store


def get_call_store() -> CallStore:
    global _store
    if _store is None:
        import redis

        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        _store = RedisCallStore(redis.from_url(url, decode_responses=True))
    return _store


def _key(call_id: str) -> str:
    return f"verify:{call_id}"


def save_call(row: dict[str, Any]) -> None:
    get_call_store().setex(_key(str(row["call_id"])), _CALL_TTL, json.dumps(row, ensure_ascii=False))


def load_call(call_id: str) -> dict[str, Any] | None:
    raw = get_call_store().get(_key(call_id))
    if not raw:
        return None
    data = json.loads(raw)
    return data if isinstance(data, dict) else None


def originate_extension_call(caller: str, callee: str, call_id: str, tenant: str = DEFAULT_TENANT) -> str:
    """Park the caller, then let the engine transfer to the callee extension."""
    return (
        "originate {origination_uuid=%s,vr_call_id=%s,vr_tenant=%s,vr_entry=extension:%s,"
        "origination_caller_id_number=%s}user/%s &park()"
        % (call_id, call_id, tenant, callee, caller, caller)
    )


def hangup_command(uuid: str) -> str:
    return f"uuid_kill {uuid}"


def call_id_from_job(command: str) -> str:
    match = _ID_RE.search(command or "")
    return match.group(1) if match else ""


def apply_verify_event(event: dict[str, Any], commands: list[Any]) -> None:
    """Fold one ESL event into the verification call record."""
    name = str(event.get("Event-Name") or "")
    call_id = str(event.get("variable_vr_call_id") or "")
    blob = f"{event.get('Job-Command', '')}\n{event.get('_body', '')}"
    if not call_id and name == "BACKGROUND_JOB":
        call_id = call_id_from_job(blob)
    if not call_id:
        return
    try:
        row = load_call(call_id) or {
            "call_id": call_id,
            "state": "queued",
            "uuids": [],
            "detail": "",
        }
    except Exception:
        return
    uuids = list(row.get("uuids") or [])
    uid = str(event.get("Unique-ID") or "")
    if uid and uid not in uuids:
        uuids.append(uid)
    row["uuids"] = uuids
    state = str(row.get("state") or "queued")
    body = str(event.get("_body") or "")
    transferred = any(getattr(cmd, "api", "") == "uuid_transfer" for cmd in commands)
    if name == "BACKGROUND_JOB":
        if "-ERR" in body:
            row["state"] = "failed"
            row["detail"] = body
        elif state == "queued":
            row["state"] = "dialing"
    elif name == "CHANNEL_PARK" and state not in {"hangup", "failed"}:
        row["state"] = "transferring" if transferred else "parked"
    elif name == "CHANNEL_ANSWER" and state == "transferring":
        row["state"] = "answered"
    elif name == "CHANNEL_ANSWER" and state not in {"answered", "hangup", "failed", "transferring"}:
        row["state"] = "dialing"
    elif name == "CHANNEL_HANGUP_COMPLETE":
        row["state"] = "hangup"
        row["detail"] = str(event.get("Hangup-Cause") or row.get("detail") or "")
    try:
        save_call(row)
    except Exception:
        return
