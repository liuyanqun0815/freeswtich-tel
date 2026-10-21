from vr_common import redis_keys as keys
from vr_common.commands import FsCommand
from vr_common.reconcile import (
    close_missing_on_fs,
    kill_for_missing_in_redis,
    reconcile,
)
from vr_common.session import CallSession, save


class _FakeStore:
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


def test_reconcile_set_diff():
    out = reconcile({"c1", "c2"}, {"c2", "c3"})
    assert out["missing_on_fs"] == {"c1"}
    assert out["missing_in_redis"] == {"c3"}


def test_close_missing_on_fs_emits_reconcile_and_deletes_session():
    store = _FakeStore()
    save(
        store,
        CallSession(
            call_id="c1",
            fs_node="fs1",
            tenant_id="t001",
            entry="e1",
            state="parked",
        ),
    )
    event = close_missing_on_fs(store, "c1", {})
    assert event["hangup_cause"] == "RECONCILE"
    assert event["vr_call_id"] == "c1"
    assert event["type"] == "hangup"
    assert keys.call_key("c1") not in store._hashes


def test_kill_for_missing_in_redis_only_with_vr_call_id():
    cmds = kill_for_missing_in_redis(
        [
            {"uuid": "u1", "vr_call_id": "c9"},
            {"uuid": "u2"},
            {"uuid": "u3", "vr_call_id": ""},
        ]
    )
    assert cmds == [FsCommand("uuid_kill", "u1")]
