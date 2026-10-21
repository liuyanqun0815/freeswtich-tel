from vr_common import redis_keys as keys
from vr_common.session import CallSession, bind_leg, delete, load, save, session_ttl_seconds


class FakeStore:
    """Minimal Redis stand-in for unit tests."""

    def __init__(self) -> None:
        self._strings: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}
        self._ttls: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self._strings.get(key)

    def set(self, key: str, value: str) -> None:
        self._strings[key] = value

    def hset(self, key: str, mapping: dict[str, str]) -> None:
        self._hashes.setdefault(key, {}).update(mapping)

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    def expire(self, key: str, ttl: int) -> None:
        self._ttls[key] = ttl

    def delete(self, key: str) -> None:
        self._strings.pop(key, None)
        self._hashes.pop(key, None)
        self._ttls.pop(key, None)


def test_redis_key_helpers():
    assert keys.call_key("c1") == "call:c1"
    assert keys.leg_key("u1") == "leg:u1"
    assert keys.CDR_STREAM == "cdr.events"


def test_session_ttl():
    assert session_ttl_seconds() == 14400


def test_save_load_roundtrip():
    store = FakeStore()
    sess = CallSession(
        call_id="c1",
        fs_node="fs1",
        tenant_id="t001",
        entry="e1",
        state="parked",
    )
    save(store, sess)
    assert store._ttls[keys.call_key("c1")] == 14400
    assert load(store, "c1") == sess


def test_bind_leg_and_delete_cleans_legs():
    store = FakeStore()
    sess = CallSession(
        call_id="c1",
        fs_node="fs1",
        tenant_id="t001",
        entry="e1",
        state="parked",
    )
    save(store, sess)
    bind_leg(store, "u1", "c1")
    bind_leg(store, "u2", "c1")
    assert store.get(keys.leg_key("u1")) == "c1"
    assert store.get(keys.leg_key("u2")) == "c1"
    delete(store, "c1")
    assert keys.call_key("c1") not in store._hashes
    assert store.get(keys.leg_key("u1")) is None
    assert store.get(keys.leg_key("u2")) is None


def test_load_missing_returns_none():
    store = FakeStore()
    assert load(store, "missing") is None


def test_load_renews_ttl():
    store = FakeStore()
    sess = CallSession(
        call_id="c1",
        fs_node="fs1",
        tenant_id="t001",
        entry="e1",
        state="parked",
    )
    save(store, sess)
    ck = keys.call_key("c1")
    store._ttls.pop(ck)
    assert load(store, "c1") == sess
    assert store._ttls[ck] == session_ttl_seconds()


def test_load_incomplete_hash_returns_none():
    store = FakeStore()
    store.hset(keys.call_key("c1"), {"call_id": "c1", "fs_node": "fs1"})
    assert load(store, "c1") is None
    assert keys.call_key("c1") not in store._ttls
