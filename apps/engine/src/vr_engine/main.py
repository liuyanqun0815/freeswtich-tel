"""Engine: fold ESL events into vr_common decisions and command lists."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vr_common.cdr import apply_event
from vr_common.commands import FsCommand, bridge
from vr_common.db import DEFAULT_TENANT
from vr_common.dialer import TaskWindow, next_action
from vr_common.dispatch import offer
from vr_common.inbound import EntryView, decide_park
from vr_common.models import ConfigRelease, Entry, PhoneNumber
from vr_common.protocol import commands_for_cancel
from vr_common.reconcile import kill_for_missing_in_redis, reconcile
from vr_common.redis_keys import CDR_STREAM
from vr_common.release import plan_apply, publish_ok
from vr_common.session import CallSession

_RELEASE_META_KEY = "__release_meta__"


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
    db: Session | None = None


def load_engine_ctx(
    session: Session, tenant_id: str = DEFAULT_TENANT
) -> EngineCtx:
    """Populate numbers/entries from DB for production park decisions."""
    numbers: dict[str, str] = {}
    for phone in session.scalars(
        select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id)
    ):
        numbers[phone.number] = "enabled" if phone.enabled else "disabled"

    entries: dict[str, EntryView] = {}
    for entry in session.scalars(
        select(Entry).where(
            Entry.tenant_id == tenant_id,
            Entry.enabled.is_(True),
        )
    ):
        entries[entry.code] = EntryView(kind=entry.kind, target=entry.target)

    return EngineCtx(entries=entries, numbers=numbers, db=session)


def refresh_engine_ctx(ctx: EngineCtx, session: Session | None = None) -> EngineCtx:
    """Reload numbers/entries into ``ctx`` (or return a fresh ctx)."""
    db = session if session is not None else ctx.db
    if db is None:
        raise RuntimeError("no database session for engine context refresh")
    loaded = load_engine_ctx(db)
    ctx.entries = loaded.entries
    ctx.numbers = loaded.numbers
    ctx.db = db
    return ctx


def parse_vr_entry(
    raw: str | None, *, kind_override: str | None = None
) -> EntryView | None:
    """Parse dialplan ``vr_entry`` (``kind:target``) into ``EntryView``."""
    if not raw or ":" not in raw:
        return None
    kind, _, target = raw.partition(":")
    if kind_override:
        kind = kind_override
    if not kind or not target:
        return None
    if kind == "did" and target == "unknown":
        return None
    return EntryView(kind=kind, target=target)


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


def _resolve_park_entry(
    event: dict[str, Any], ctx: EngineCtx, dest: str
) -> EntryView | None:
    raw = event.get("variable_vr_entry")
    kind_ov = event.get("variable_vr_entry_kind")
    if raw:
        parsed = parse_vr_entry(raw, kind_override=kind_ov)
        if parsed is not None:
            return parsed
    return ctx.entries.get(dest)


def _send_plan_via_transport(ctx: EngineCtx, plan: list[str]) -> None:
    transport = ctx.transport
    if transport is None:
        raise RuntimeError("ESL transport is not connected")
    for cmd in plan:
        line = f"bgapi {cmd}".strip()
        transport.sendall((line + "\n\n").encode("utf-8"))


def apply_pending_release(
    ctx: EngineCtx,
    release_id: int,
    *,
    db: Session | None = None,
    command_ok: bool = True,
    reged: bool = False,
) -> str:
    """Execute a pending ConfigRelease via ESL transport; mark applied or failed.

    ``command_ok`` / ``reged`` are engine-local ESL outcomes (not API client input).
    """
    session = db if db is not None else ctx.db
    if session is None:
        raise RuntimeError("no database session for release apply")

    row = session.get(ConfigRelease, release_id)
    if row is None or row.tenant_id != DEFAULT_TENANT:
        raise ValueError(f"release not found: {release_id}")
    if row.status != "pending":
        return row.status

    try:
        payload = json.loads(row.snapshot_json)
    except json.JSONDecodeError:
        payload = {}
    meta = payload.get(_RELEASE_META_KEY) if isinstance(payload, dict) else None
    if not isinstance(meta, dict):
        row.status = "failed"
        row.applied_at = None
        session.commit()
        return "failed"

    kind = str(meta.get("kind") or "")
    name = str(meta.get("name") or "")
    needs_register = bool(meta.get("needs_register", True))
    plan = plan_apply(kind, name)
    _send_plan_via_transport(ctx, plan)

    ok = bool(command_ok) and publish_ok(reged, needs_register)
    row.status = "applied" if ok else "failed"
    row.applied_at = datetime.now(timezone.utc) if ok else None
    session.commit()
    return row.status


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
            entry = _resolve_park_entry(event, ctx, dest)
            if dest in ctx.numbers:
                number_status = ctx.numbers[dest]
            elif entry is not None:
                number_status = "enabled"
            else:
                number_status = "disabled"
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
    elif event_name == "VR_RELEASE_APPLY":
        release_id = int(event["release_id"])
        apply_pending_release(
            ctx,
            release_id,
            db=ctx.db,
            command_ok=bool(event.get("command_ok", True)),
            reged=bool(event.get("reged", False)),
        )

    if cmds:
        ctx.sent.extend(cmds)

    if call_id and event_name not in {
        "VR_BRIDGE",
        "VR_CANCEL",
        "VR_DIAL",
        "VR_RECONCILE",
        "VR_RELEASE_APPLY",
    }:
        row = ctx.cdr_rows.get(call_id, {})
        updated = apply_event(row, _cdr_payload(event, call_id))
        ctx.cdr_rows[call_id] = updated
        _xadd_cdr(ctx, updated)

    return cmds
