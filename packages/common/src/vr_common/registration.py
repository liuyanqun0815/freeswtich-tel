"""SIP registration state shared by the engine and the API."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Protocol

_USER_RE = re.compile(r"([A-Za-z0-9_.+-]+)@")


class RegStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def members(self) -> set[str]: ...

    def add(self, ext: str) -> None: ...


class MemoryRegStore:
    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.exts: set[str] = set()

    def get(self, key: str) -> str | None:
        return self.rows.get(key)

    def set(self, key: str, value: str) -> None:
        self.rows[key] = value

    def members(self) -> set[str]:
        return set(self.exts)

    def add(self, ext: str) -> None:
        self.exts.add(ext)


class RedisRegStore:
    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> str | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode("utf-8")

    def set(self, key: str, value: str) -> None:
        self._client.set(key, value)

    def members(self) -> set[str]:
        raw = self._client.smembers("reg:exts")
        return {item if isinstance(item, str) else item.decode("utf-8") for item in raw}

    def add(self, ext: str) -> None:
        self._client.sadd("reg:exts", ext)


_store: RegStore | None = None


def set_reg_store(store: RegStore | None) -> None:
    global _store
    _store = store


def get_reg_store() -> RegStore:
    global _store
    if _store is None:
        import redis

        url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        _store = RedisRegStore(redis.from_url(url, decode_responses=True))
    return _store


def _key(ext: str) -> str:
    return f"reg:ext:{ext}"


def _write(ext: str, registered: bool, contact: str | None) -> dict[str, Any]:
    store = get_reg_store()
    previous = _read(ext)
    row = {
        "ext": ext,
        "registered": registered,
        "contact": previous.get("contact", "") if contact is None else contact,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    store.set(_key(ext), json.dumps(row, ensure_ascii=False))
    store.add(ext)
    return row


def _read(ext: str) -> dict[str, Any]:
    raw = get_reg_store().get(_key(ext))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def mark_registered(ext: str, contact: str | None = None) -> dict[str, Any]:
    return _write(ext, True, contact)


def mark_unregistered(ext: str) -> dict[str, Any]:
    return _write(ext, False, "")


def is_registered(ext: str) -> bool:
    return bool(_read(ext).get("registered"))


def registration_view(ext: str) -> dict[str, Any]:
    row = _read(ext)
    return {
        "ext": ext,
        "registered": bool(row.get("registered")),
        "contact": str(row.get("contact") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def list_registrations() -> list[dict[str, Any]]:
    return [registration_view(ext) for ext in sorted(get_reg_store().members())]


def replace_registered(exts: set[str]) -> None:
    """Heartbeat snapshot: listed users are registered, previously seen others are not."""
    known = get_reg_store().members()
    for ext in exts:
        mark_registered(ext)
    for ext in known - exts:
        mark_unregistered(ext)


def parse_sofia_registrations(body: str) -> set[str]:
    return {match.group(1) for match in _USER_RE.finditer(body or "")}


def extension_from_register_event(event: dict[str, Any]) -> str:
    for key in ("from-user", "username", "to-user", "user"):
        raw = event.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip().split("@", 1)[0]
    return ""
