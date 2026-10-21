"""Temporary SIP credentials for browser softphone registration."""

from __future__ import annotations

import os
import secrets
from typing import Any, MutableMapping

from vr_common import redis_keys as keys
from vr_common.xmlbuild import a1_hash

DEFAULT_SIP_CRED_TTL = 43200
DEFAULT_WSS_URL = "wss://47.253.82.114:7443"
DEFAULT_STUN_URL = "stun:stun.l.google.com:19302"

_store: MutableMapping[str, Any] = {}


def get_store() -> MutableMapping[str, Any]:
    return _store


def set_store(store: MutableMapping[str, Any]) -> None:
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


def store_cred(
    store: MutableMapping[str, Any],
    domain: str,
    ext: str,
    password: str,
    a1: str,
    ttl: int = DEFAULT_SIP_CRED_TTL,
    agent_id: str = "",
) -> None:
    _ = ttl  # plain dict store; Redis adapter may use setex later
    key = keys.sipcred_key(domain, ext)
    store[key] = {"password": password, "a1": a1, "agent_id": agent_id}


def get_cred(
    store: MutableMapping[str, Any],
    domain: str,
    ext: str,
) -> dict[str, Any] | None:
    cred = store.get(keys.sipcred_key(domain, ext))
    if not isinstance(cred, dict):
        return None
    return cred


def lookup_a1(
    store: MutableMapping[str, Any],
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
    return a1 if isinstance(a1, str) else None


def delete_cred(
    store: MutableMapping[str, Any],
    domain: str,
    ext: str,
) -> None:
    store.pop(keys.sipcred_key(domain, ext), None)


def issue_and_store(
    store: MutableMapping[str, Any],
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
