from dataclasses import dataclass
from typing import Protocol

from vr_common import redis_keys as keys

_LEGS_FIELD = "legs"
_LEG_SEP = ","


class SessionStore(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def hset(self, key: str, mapping: dict[str, str]) -> None: ...

    def hgetall(self, key: str) -> dict[str, str]: ...

    def expire(self, key: str, ttl: int) -> None: ...

    def delete(self, key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class CallSession:
    call_id: str
    fs_node: str
    tenant_id: str
    entry: str
    state: str


def session_ttl_seconds() -> int:
    return 14400


def _session_fields(session: CallSession) -> dict[str, str]:
    return {
        "call_id": session.call_id,
        "fs_node": session.fs_node,
        "tenant_id": session.tenant_id,
        "entry": session.entry,
        "state": session.state,
    }


def _parse_legs(raw: str) -> list[str]:
    if not raw:
        return []
    return [part for part in raw.split(_LEG_SEP) if part]


def _merge_legs(existing: str, uuid: str) -> str:
    legs = _parse_legs(existing)
    if uuid not in legs:
        legs.append(uuid)
    return _LEG_SEP.join(legs)


def save(store: SessionStore, session: CallSession) -> None:
    ck = keys.call_key(session.call_id)
    existing = store.hgetall(ck)
    payload = _session_fields(session)
    if _LEGS_FIELD in existing:
        payload[_LEGS_FIELD] = existing[_LEGS_FIELD]
    store.hset(ck, payload)
    store.expire(ck, session_ttl_seconds())


def load(store: SessionStore, call_id: str) -> CallSession | None:
    data = store.hgetall(keys.call_key(call_id))
    if not data or "call_id" not in data:
        return None
    return CallSession(
        call_id=data["call_id"],
        fs_node=data["fs_node"],
        tenant_id=data["tenant_id"],
        entry=data["entry"],
        state=data["state"],
    )


def bind_leg(store: SessionStore, uuid: str, call_id: str) -> None:
    ck = keys.call_key(call_id)
    store.set(keys.leg_key(uuid), call_id)
    existing = store.hgetall(ck)
    existing[_LEGS_FIELD] = _merge_legs(existing.get(_LEGS_FIELD, ""), uuid)
    store.hset(ck, existing)
    store.expire(ck, session_ttl_seconds())
    store.expire(keys.leg_key(uuid), session_ttl_seconds())


def delete(store: SessionStore, call_id: str) -> None:
    ck = keys.call_key(call_id)
    data = store.hgetall(ck)
    for uuid in _parse_legs(data.get(_LEGS_FIELD, "")):
        store.delete(keys.leg_key(uuid))
    store.delete(ck)
