"""Reconcile Redis call sessions against live FreeSWITCH channels."""

from __future__ import annotations

from typing import Any

from vr_common.cdr import apply_event
from vr_common.commands import FsCommand
from vr_common.session import SessionStore, delete


def reconcile(redis_ids: set[str], fs_ids: set[str]) -> dict[str, set[str]]:
    return {
        "missing_on_fs": set(redis_ids) - set(fs_ids),
        "missing_in_redis": set(fs_ids) - set(redis_ids),
    }


def close_missing_on_fs(
    store: SessionStore,
    call_id: str,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Emit RECONCILE hangup CDR event and delete the Redis session."""
    event = {"type": "hangup", "vr_call_id": call_id, "hangup_cause": "RECONCILE"}
    apply_event(row, event)
    delete(store, call_id)
    return event


def kill_for_missing_in_redis(channels: list[dict[str, Any]]) -> list[FsCommand]:
    """uuid_kill channels present on FS but absent from Redis when vr_call_id is set."""
    cmds: list[FsCommand] = []
    for ch in channels:
        vr_call_id = ch.get("vr_call_id")
        uuid = ch.get("uuid")
        if uuid and vr_call_id:
            cmds.append(FsCommand("uuid_kill", str(uuid)))
    return cmds
