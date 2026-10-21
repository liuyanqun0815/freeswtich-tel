"""Temporary SIP credentials for browser softphone registration."""

from __future__ import annotations

import json
import os
import secrets
import time
from typing import Any, Protocol

from vr_common import redis_keys as keys
from vr_common.xmlbuild import a1_hash

DEFAULT_SIP_CRED_TTL = 43200
DEFAULT_WSS_URL = "wss://47.253.82.114:7443"
DEFAULT_STUN_URL = "stun:stun.l.google.com:19302"
DEFAULT_REDIS_URL = "redis://127.0.0.1:6379/0"


class CredStore(Protocol):
    """Shared credential backend (Redis in production; MemoryCredStore in tests)."""

    def get(self, key: str) -> str | None: ...

    def setex(self, key: str, ttl: int, value: str) -> None: ...

    def delete(self, key: str) -> None: ...


class MemoryCredStore:
    """In-memory CredStore that honors TTL via expires_at."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() >= expires_at:
            self._entries.pop(key, None)
            return None
        return value

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._entries[key] = (value, time.time() + max(0, ttl))

    def delete(self, key: str) -> None:
        self._entries.pop(key, None)


class RedisCredStore:
    """Redis-backed CredStore using SETEX so api and config share keys."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> str | None:
        raw = self._client.get(key)
        if raw is None:
            return None
        return raw if isinstance(raw, str) else raw.decode("utf-8")

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._client.setex(key, ttl, value)

    def delete(self, key: str) -> None:
        self._client.delete(key)


_store: CredStore | None = None


def create_redis_cred_store(url: str | None = None) -> RedisCredStore:
    import redis

    redis_url = url or os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    client = redis.from_url(redis_url, decode_responses=True)
    return RedisCredStore(client)


def get_store() -> CredStore:
    """Production default: Redis via REDIS_URL. Tests inject via set_store."""
    global _store
    if _store is None:
        _store = create_redis_cred_store()
    return _store


def set_store(store: CredStore | None) -> None:
    global _store
    _store = store


def sip_cred_ttl_seconds() -> int:
    return int(os.environ.get("SIP_CRED_TTL_SECONDS", str(DEFAULT_SIP_CRED_TTL)))


def wss_url() -> str:
    return os.environ.get("WSS_URL", DEFAULT_WSS_URL)


def ice_servers() -> list[dict[str, str]]:
    return [{"urls": DEFAULT_STUN_URL}]


def domain_for_tenant(tenant_id: str) -> str:
    return f"{tenant_id}.vr.local"


def issue_sip_password() -> str:
    return secrets.token_urlsafe(16)


def _encode_cred(password: str, a1: str, agent_id: str) -> str:
    return json.dumps({"password": password, "a1": a1, "agent_id": agent_id})


def _decode_cred(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def store_cred(
    store: CredStore,
    domain: str,
    ext: str,
    password: str,
    a1: str,
    ttl: int = DEFAULT_SIP_CRED_TTL,
    agent_id: str = "",
) -> None:
    key = keys.sipcred_key(domain, ext)
    store.setex(key, ttl, _encode_cred(password, a1, agent_id))


def get_cred(
    store: CredStore,
    domain: str,
    ext: str,
) -> dict[str, Any] | None:
    return _decode_cred(store.get(keys.sipcred_key(domain, ext)))


def lookup_a1(
    store: CredStore,
    domain: str,
    ext: str,
    password: str,
) -> str | None:
    cred = get_cred(store, domain, ext)
    if cred is None:
        return None
    if cred.get("password") != password:
        return None
    a1 = cred.get("a1")
    return a1 if isinstance(a1, str) and a1 else None


def delete_cred(
    store: CredStore,
    domain: str,
    ext: str,
) -> None:
    store.delete(keys.sipcred_key(domain, ext))


def issue_and_store(
    store: CredStore,
    *,
    domain: str,
    ext: str,
    agent_id: str,
    ttl: int | None = None,
) -> str:
    password = issue_sip_password()
    a1 = a1_hash(ext, domain, password)
    store_cred(
        store,
        domain,
        ext,
        password,
        a1,
        ttl=sip_cred_ttl_seconds() if ttl is None else ttl,
        agent_id=agent_id,
    )
    return password
