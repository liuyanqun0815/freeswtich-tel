"""Engine: fold ESL events into vr_common decisions and command lists."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from vr_common.cdr import apply_event
from vr_common.commands import FsCommand, bridge
from vr_common.dialer import TaskWindow, next_action
from vr_common.dispatch import offer
from vr_common.inbound import EntryView, decide_park
from vr_common.protocol import commands_for_cancel
from vr_common.reconcile import kill_for_missing_in_redis, reconcile
from vr_common.redis_keys import CDR_STREAM
from vr_common.session import CallSession


@dataclass
class EngineCtx:
    """In-memory engine context; ``transport`` is a test double in unit tests."""

    entries: dict[str, EntryView]
    numbers: dict[str, str]
    sessions: dict[str, CallSession] = field(default_factory=dict)
    sent: list[FsCommand] = field(default_factory=list)
    transport: Any = None
    queue_size: int = 0
    capacity: int = 64
    cdr_rows: dict[str, dict[str, Any]] = field(default_factory=dict)
    streams: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {CDR_STREAM: []}
    )


def retry_or_kill(attempts: int) -> str:
    """bgapi timeout policy: first miss retries; further misses kill the call."""
    return "retry" if attempts == 0 else "kill"


def _call_key(event: dict[str, Any]) -> str:
    return event.get("variable_vr_call_id") or event.get("Unique-ID") or ""


def _xadd_cdr(ctx: EngineCtx, row: dict[str, Any]) -> None:
    ctx.streams.setdefault(CDR_STREAM, []).append(dict(row))


def _cdr_payload(event: dict[str, Any], call_id: str) -> dict[str, Any]:
    name = event.get("Event-Name", "")
    base: dict[str, Any] = {"vr_call_id": call_id}
    if name == "CHANNEL_PARK":
        base["type"] = "start"
        base["caller"] = event.get("Caller-Caller-ID-Number", "")
        base["callee"] = event.get("Caller-Destination-Number", "")
        base["direction"] = event.get("Caller-Direction", "")
    elif name == "CHANNEL_ANSWER":
        base["type"] = "answer"
    elif name == "CHANNEL_HANGUP_COMPLETE":
        base["type"] = "hangup"
        base["hangup_cause"] = event.get("Hangup-Cause", "")
    else:
        base["type"] = name or "event"
    return base


def handle_event(event: dict[str, Any], ctx: EngineCtx) -> list[FsCommand]:
    """Route one ESL event through shared decisions; append cmds to ``ctx.sent``."""
    event_name = event.get("Event-Name", "")
    decision = offer(ctx.queue_size, ctx.capacity, event_name)
    if decision == "drop":
        return []

    call_id = _call_key(event)
    uuid = event.get("Unique-ID", "")
    cmds: list[FsCommand] = []

    if event_name == "CHANNEL_PARK":
        existing = ctx.sessions.get(call_id)
        if existing is None:
            dest = event.get("Caller-Destination-Number", "")
            entry = ctx.entries.get(dest)
            number_status = ctx.numbers.get(dest, "disabled")
            direction = event.get("Caller-Direction", "")
            cmds = decide_park(
                None,
                direction,
                entry,
                number_status,
                uuid,
                call_id,
            )
    elif event_name == "VR_BRIDGE":
        cmds = bridge(str(event["a_uuid"]), str(event["b_uuid"]))
    elif event_name == "VR_CANCEL":
        cmds = commands_for_cancel(uuid)
    elif event_name == "VR_DIAL":
        action = next_action(
            datetime.fromisoformat(event["now"]),
            TaskWindow(),
            str(event["number"]),
            event.get("blacklist") or [],
            int(event.get("success_count_24h", 0)),
            gateway=str(event["gateway"]),
            caller=str(event["caller"]),
            call_id=call_id or str(event.get("call_id", "")),
            uuid=uuid,
        )
        if action.get("status") == "originate":
            cmds = [FsCommand("originate", str(action["originate"]).removeprefix("originate "))]
    elif event_name == "VR_RECONCILE":
        redis_ids = set(event.get("redis_ids") or ctx.sessions.keys())
        fs_ids = set(event.get("fs_ids") or [])
        diff = reconcile(redis_ids, fs_ids)
        channels = event.get("missing_channels") or []
        if diff["missing_in_redis"]:
            cmds = kill_for_missing_in_redis(channels)

    if cmds:
        ctx.sent.extend(cmds)

    if call_id and event_name not in {"VR_BRIDGE", "VR_CANCEL", "VR_DIAL", "VR_RECONCILE"}:
        row = ctx.cdr_rows.get(call_id, {})
        updated = apply_event(row, _cdr_payload(event, call_id))
        ctx.cdr_rows[call_id] = updated
        _xadd_cdr(ctx, updated)

    return cmds
