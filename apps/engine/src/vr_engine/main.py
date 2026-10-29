"""Engine: fold ESL events into vr_common decisions and command lists."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vr_common.cdr import apply_event, upsert_call_record
from vr_common.commands import FsCommand, bridge
from vr_common.db import DEFAULT_TENANT
from vr_common.dialer import TaskWindow, next_action
from vr_common.dispatch import offer
from vr_common.blind_transfer import pick_extension_leg_uuid
from vr_common.esljobs import drain_jobs
from vr_common.sipcred import domain_for_tenant
from vr_common.inbound import EntryView, decide_park, robot_stream_commands
from vr_common.ivr import step as ivr_step
from vr_common.ivr_route import pick_bridge_extension
from vr_common.models import ConfigRelease, Entry, IvrPublish, PhoneNumber, SkillGroup
from vr_common.protocol import commands_for_cancel, commands_for_transfer
from vr_common.reconcile import kill_for_missing_in_redis, reconcile
from vr_common.registration import (
    extension_from_register_event,
    is_registered,
    mark_registered,
    mark_unregistered,
    parse_sofia_registrations,
    replace_registered,
)
from vr_common.redis_keys import CDR_STREAM
from vr_common.release import plan_apply, publish_ok
from vr_common.session import CallSession
from vr_common.verify_call import apply_verify_event

logger = logging.getLogger("vr.engine")

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
    ivr_snapshots: dict[str, tuple[int, dict[str, Any]]] = field(default_factory=dict)
    ivr_buffers: dict[str, str] = field(default_factory=dict)
    skill_groups: dict[str, list[str]] = field(default_factory=dict)
    pending_active: dict[str, list[FsCommand]] = field(default_factory=dict)


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


def _epoch(event: dict[str, Any], key: str) -> datetime | None:
    raw = event.get(key) or ""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > 4_000_000_000:
        return None
    return datetime.fromtimestamp(value, timezone.utc)


def _optional_int(event: dict[str, Any], key: str) -> int | None:
    raw = event.get(key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _cdr_payload(event: dict[str, Any], call_id: str) -> dict[str, Any]:
    name = event.get("Event-Name", "")
    base: dict[str, Any] = {"vr_call_id": call_id}
    tenant = event.get("variable_vr_tenant")
    if tenant:
        base["tenant_id"] = tenant
    if name == "CHANNEL_PARK":
        base["type"] = "start"
        base["caller"] = event.get("Caller-Caller-ID-Number", "")
        base["callee"] = event.get("Caller-Destination-Number", "")
        base["direction"] = event.get("Caller-Direction", "")
        started = _epoch(event, "variable_start_epoch")
        if started is not None:
            base["started_at"] = started
    elif name == "CHANNEL_ANSWER":
        base["type"] = "answer"
        answered = _epoch(event, "variable_answer_epoch")
        if answered is not None:
            base["answered_at"] = answered
    elif name == "CHANNEL_HANGUP_COMPLETE":
        base["type"] = "hangup"
        base["caller"] = event.get("Caller-Caller-ID-Number", "")
        base["callee"] = event.get("Caller-Destination-Number", "")
        base["direction"] = event.get("Caller-Direction", "")
        base["hangup_cause"] = event.get("Hangup-Cause") or event.get("variable_hangup_cause") or ""
        for field_name, source in (
            ("duration", "variable_duration"),
            ("billsec", "variable_billsec"),
        ):
            number = _optional_int(event, source)
            if number is not None:
                base[field_name] = number
        for field_name, source in (
            ("started_at", "variable_start_epoch"),
            ("answered_at", "variable_answer_epoch"),
            ("ended_at", "variable_end_epoch"),
        ):
            instant = _epoch(event, source)
            if instant is not None:
                base[field_name] = instant
    else:
        base["type"] = name or "event"
    return base


def _is_primary_cdr_leg(event: dict[str, Any]) -> bool:
    """B-leg hangups share vr_call_id but must not replace the caller's CDR."""
    call_id = event.get("variable_vr_call_id") or ""
    uuid = event.get("Unique-ID") or ""
    return not (call_id and uuid and call_id != uuid)


def park_decision_reason(
    raw: str | None,
    entry: EntryView | None,
    number_status: str,
    cmds: list[FsCommand],
) -> str:
    """Why a parked call was transferred or rejected."""
    if entry is None:
        if raw and ":" not in raw:
            return "vr-entry-unparsed"
        return "unknown-destination"
    if number_status == "disabled":
        return "number-disabled"
    if any(cmd.api == "uuid_transfer" for cmd in cmds):
        return "transfer"
    if any(cmd.api == "uuid_kill" for cmd in cmds):
        return "rejected"
    return "handled"


def _explain_park_entry(
    event: dict[str, Any], ctx: EngineCtx, dest: str
) -> tuple[EntryView | None, str]:
    """Resolve a parked call and say why it did or did not match an entry."""
    raw = event.get("variable_vr_entry")
    kind_ov = event.get("variable_vr_entry_kind")
    if raw:
        parsed = parse_vr_entry(raw, kind_override=kind_ov)
        if parsed is not None:
            return parsed, "vr_entry"
        why = "vr_entry_no_colon" if ":" not in str(raw) else "vr_entry_invalid"
    else:
        why = "vr_entry_missing"
    fallback = ctx.entries.get(dest) if dest else None
    if fallback is not None:
        return fallback, "number_entry"
    number_state = ctx.numbers.get(dest) if dest else None
    if number_state is None:
        why = f"{why},number_unknown"
    elif number_state == "disabled":
        why = f"{why},number_disabled"
    else:
        why = f"{why},entry_missing"
    return None, why


def _resolve_park_entry(
    event: dict[str, Any], ctx: EngineCtx, dest: str
) -> EntryView | None:
    entry, _reason = _explain_park_entry(event, ctx, dest)
    return entry


_REGED_ATTEMPTS = 3


def esl_api(transport: Any, command: str) -> str:
    """Run one synchronous ESL ``api`` and return the body."""
    transport.sendall(f"api {command}\n\n".encode("utf-8"))
    buf = b""
    while b"\n\n" not in buf:
        chunk = transport.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > 1_000_000:
            break
    header, _, rest = buf.partition(b"\n\n")
    length = 0
    for line in header.decode("utf-8", errors="replace").splitlines():
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    body = rest
    while length and len(body) < length:
        more = transport.recv(4096)
        if not more:
            break
        body += more
    if length:
        body = body[:length]
    return body.decode("utf-8", errors="replace")


def gateway_is_reged(transport: Any, name: str) -> bool:
    body = esl_api(transport, f"sofia status gateway {name}")
    return "REGED" in body


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


def _resolve_blind_transfer_leg(ctx: EngineCtx, from_ext: str) -> str | None:
    if ctx.transport is None:
        logger.warning("blind transfer skipped: no ESL transport from=%s", from_ext)
        return None
    try:
        body = esl_api(ctx.transport, "show channels as json")
    except Exception:
        logger.exception("blind transfer show channels failed from=%s", from_ext)
        return None
    uuid = pick_extension_leg_uuid(body, from_ext)
    if not uuid:
        logger.warning("blind transfer no active leg from=%s", from_ext)
    return uuid


def _commands_for_blind_transfer(
    ctx: EngineCtx,
    from_ext: str,
    *,
    to_ext: str = "",
    skill_group_id: Any = None,
) -> list[FsCommand]:
    """Resolve the agent leg UUID then blind-transfer to an extension or skill-group member."""
    uuid = _resolve_blind_transfer_leg(ctx, from_ext)
    if not uuid:
        return []
    target = (to_ext or "").strip()
    if target:
        return [_extension_transfer(uuid, target)]
    members = [m for m in _skill_group_members(ctx, skill_group_id) if m != from_ext]
    chosen = pick_bridge_extension("", members, registered=is_registered)
    if chosen is None:
        logger.warning(
            "blind transfer skill group has no online member from=%s skill_group_id=%s",
            from_ext,
            skill_group_id,
        )
        return []
    return [_extension_transfer(uuid, chosen)]


def drain_flush_commands(ctx: EngineCtx) -> list[FsCommand]:
    """Take ESL jobs enqueued by the API (directory flush, blind transfer, raw cmds)."""
    try:
        jobs = drain_jobs()
    except Exception:
        return []
    cmds: list[FsCommand] = []
    for job in jobs:
        job_type = str(job.get("type") or "")
        if job_type == "blind_transfer":
            from_ext = str(job.get("from_ext") or "").strip()
            to_ext = str(job.get("to_ext") or "").strip()
            skill_group_id = job.get("skill_group_id")
            if from_ext and (to_ext or skill_group_id is not None):
                cmds.extend(
                    _commands_for_blind_transfer(
                        ctx, from_ext, to_ext=to_ext, skill_group_id=skill_group_id
                    )
                )
            continue
        command = job.get("cmd")
        if isinstance(command, str) and command:
            api, _, args = command.partition(" ")
            cmds.append(FsCommand(api, args))
    return cmds


def advance_pending_releases(ctx: EngineCtx) -> list[str]:
    """Send pending gateway/ACL plans and mark them from the ESL REGED result."""
    session = ctx.db
    if session is None or ctx.transport is None:
        return []
    rows = session.scalars(
        select(ConfigRelease)
        .where(
            ConfigRelease.tenant_id == DEFAULT_TENANT,
            ConfigRelease.status == "pending",
        )
        .order_by(ConfigRelease.id)
    ).all()
    results: list[str] = []
    for row in rows:
        try:
            payload = json.loads(row.snapshot_json)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        meta = payload.get(_RELEASE_META_KEY)
        if not isinstance(meta, dict):
            row.status = "failed"
            row.applied_at = None
            session.commit()
            results.append("failed")
            continue
        kind = str(meta.get("kind") or "")
        name = str(meta.get("name") or "")
        needs_register = bool(meta.get("needs_register", True))
        if not meta.get("dispatched"):
            _send_plan_via_transport(ctx, plan_apply(kind, name))
            meta["dispatched"] = True
            meta["attempts"] = int(meta.get("attempts") or 0)
        if not needs_register:
            row.status = "applied"
            row.applied_at = datetime.now(timezone.utc)
            payload[_RELEASE_META_KEY] = meta
            row.snapshot_json = json.dumps(payload, ensure_ascii=False)
            session.commit()
            results.append("applied")
            continue
        reged = False
        try:
            reged = gateway_is_reged(ctx.transport, name)
        except Exception:
            reged = False
        if publish_ok(reged, True):
            row.status = "applied"
            row.applied_at = datetime.now(timezone.utc)
            results.append("applied")
        else:
            attempts = int(meta.get("attempts") or 0) + 1
            meta["attempts"] = attempts
            if attempts >= _REGED_ATTEMPTS:
                row.status = "failed"
                row.applied_at = None
                results.append("failed")
            else:
                results.append("pending")
        payload[_RELEASE_META_KEY] = meta
        row.snapshot_json = json.dumps(payload, ensure_ascii=False)
        session.commit()
    return results


def sync_registrations(ctx: EngineCtx) -> None:
    """Reconcile extension registration from sofia on each heartbeat."""
    transport = ctx.transport
    if transport is None or not hasattr(transport, "recv"):
        return
    profile = os.environ.get("FS_SIP_PROFILE", "internal")
    try:
        body = esl_api(transport, f"sofia status profile {profile} reg")
    except Exception:
        logger.exception("sofia reg sync failed profile=%s", profile)
        return
    if "Registrations" not in body and "Total items returned" not in body:
        logger.info("sofia reg sync skipped profile=%s reason=not-a-registration-list", profile)
        return
    try:
        found = parse_sofia_registrations(body)
        logger.info("sofia reg sync profile=%s extensions=%s", profile, ",".join(found) or "-")
        replace_registered(found)
    except Exception:
        logger.exception("sofia reg sync apply failed profile=%s", profile)
        return


def run_maintenance(ctx: EngineCtx) -> list[FsCommand]:
    flush = drain_flush_commands(ctx)
    advance_pending_releases(ctx)
    sync_registrations(ctx)
    return flush


def _load_ivr(ctx: EngineCtx, flow_key: str) -> tuple[int, dict[str, Any]] | None:
    if ctx.db is not None and str(flow_key).isdigit():
        row = ctx.db.scalars(
            select(IvrPublish)
            .where(
                IvrPublish.flow_id == int(flow_key),
                IvrPublish.tenant_id == DEFAULT_TENANT,
            )
            .order_by(IvrPublish.version.desc())
        ).first()
        if row is None:
            return None
        try:
            snapshot = json.loads(row.snapshot_json)
        except json.JSONDecodeError:
            return None
        if not isinstance(snapshot, dict):
            return None
        loaded = (row.version, snapshot)
        ctx.ivr_snapshots[flow_key] = loaded
        return loaded
    return ctx.ivr_snapshots.get(flow_key)


def _find_ivr_node(snapshot: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    if snapshot.get("id") == node_id:
        return snapshot
    for child in snapshot.get("children") or []:
        if isinstance(child, dict):
            found = _find_ivr_node(child, node_id)
            if found is not None:
                return found
    return None


def _node_prompt(snapshot: dict[str, Any], node_id: str) -> str:
    node = _find_ivr_node(snapshot, node_id)
    if node is None:
        return ""
    prompt = node.get("prompt") or node.get("file") or ""
    return str(prompt) if prompt else ""


def _broadcast(uuid: str, path: str) -> FsCommand:
    return FsCommand("uuid_broadcast", f"{uuid} {path} aleg")


def _flush_pending_active(ctx: EngineCtx, call_id: str) -> list[FsCommand]:
    cmds = ctx.pending_active.pop(call_id, [])
    if cmds:
        logger.info("channel active call_id=%s cmds=%s", call_id, " | ".join(f"{c.api} {c.args}" for c in cmds))
    return cmds


def _stream_play_commands(event: dict[str, Any], uuid: str) -> list[FsCommand]:
    try:
        body = json.loads(event.get("_body") or "{}")
    except json.JSONDecodeError:
        body = None
    path = str(body.get("file") or "") if isinstance(body, dict) else ""
    if not path or not uuid:
        logger.warning("audio_stream play ignored uuid=%s body=%s", uuid or "-", (event.get("_body") or "")[:200])
        return []
    return [_broadcast(uuid, path)]


def _ai_command(ctx: EngineCtx, event: dict[str, Any], uuid: str) -> list[FsCommand]:
    kind = event.get("type")
    if uuid and kind == "break":
        return commands_for_cancel(uuid)
    if uuid and kind == "hangup":
        return [FsCommand("uuid_kill", uuid)]
    if uuid and kind == "transfer":
        return commands_for_transfer(uuid) + _bridge_extension_or_queue(ctx, uuid, "", event.get("skill_group_id"))
    logger.warning("ai command ignored type=%s uuid=%s", kind or "-", uuid or "-")
    return []


def _skill_group_members(ctx: EngineCtx, skill_group_id: Any) -> list[str]:
    key = str(skill_group_id or "").strip()
    if not key:
        return []
    cached = ctx.skill_groups.get(key)
    if cached is not None:
        return list(cached)
    if ctx.db is None or not key.isdigit():
        return []
    row = ctx.db.get(SkillGroup, int(key))
    if row is None or row.tenant_id != DEFAULT_TENANT:
        return []
    try:
        data = json.loads(row.members_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    members = [str(item).strip() for item in data if str(item).strip()]
    ctx.skill_groups[key] = members
    return members


def _bridge_extension_or_queue(
    ctx: EngineCtx, uuid: str, preferred: str, skill_group_id: Any
) -> list[FsCommand]:
    members = _skill_group_members(ctx, skill_group_id)
    chosen = pick_bridge_extension(preferred, members, registered=is_registered)
    if chosen is None:
        return decide_park("inbound", None, "disabled", uuid)
    return _bridge_extension_commands(uuid, chosen)


def _ivr_follow(
    ctx: EngineCtx,
    uuid: str,
    call_id: str,
    snapshot: dict[str, Any],
    result: dict[str, Any],
    node_id: str,
) -> tuple[str, list[FsCommand]]:
    action = str(result.get("action") or "")
    next_node = str(result.get("node_id") or node_id)
    cmds: list[FsCommand] = []
    if action == "replay":
        prompt = _node_prompt(snapshot, node_id)
        if prompt:
            cmds.append(_broadcast(uuid, prompt))
        return node_id, cmds
    if action in {"parent", "goto"}:
        kind = str(result.get("kind") or "")
        target = result.get("target")
        if kind == "hangup":
            cmds.append(FsCommand("uuid_kill", uuid))
        elif kind == "playback" and target:
            cmds.append(_broadcast(uuid, str(target)))
            return node_id, cmds
        elif kind == "queue" and target:
            cmds.append(FsCommand("uuid_setvar", f"{uuid} vr_skill_group={target}"))
        elif kind == "extension" and target:
            cmds.extend(
                _bridge_extension_or_queue(
                    ctx, uuid, str(target), result.get("skill_group_id")
                )
            )
        elif kind == "robot" and target:
            cmds.extend(robot_stream_commands(uuid, call_id, str(target)))
        else:
            prompt = str(result.get("prompt") or "") or _node_prompt(snapshot, next_node)
            if prompt:
                cmds.append(_broadcast(uuid, prompt))
            cmds.append(FsCommand("uuid_setvar", f"{uuid} vr_ivr_node={next_node}"))
        return next_node, cmds
    if action == "complete":
        cmds.append(FsCommand("uuid_setvar", f"{uuid} vr_collect={result.get('buffer', '')}"))
        return node_id, cmds
    return node_id, cmds


def _enter_ivr(ctx: EngineCtx, flow_key: str, uuid: str, call_id: str) -> list[FsCommand]:
    loaded = _load_ivr(ctx, flow_key)
    if loaded is None:
        return decide_park("inbound", None, "disabled", uuid)
    version, snapshot = loaded
    node_id = str(snapshot.get("id") or "root")
    ctx.sessions[call_id] = CallSession(
        call_id=call_id,
        fs_node="",
        tenant_id=DEFAULT_TENANT,
        entry=f"ivr:{flow_key}",
        state=f"ivr:{node_id}",
        ivr_version=version,
    )
    prompt = _node_prompt(snapshot, node_id)
    if prompt:
        ctx.pending_active[call_id] = [_broadcast(uuid, prompt)]
    return [
        FsCommand("uuid_answer", uuid),
        FsCommand("uuid_setvar", f"{uuid} vr_ivr_version={version}"),
    ]


def _extension_transfer(uuid: str, ext: str) -> FsCommand:
    tenant = os.environ.get("DEFAULT_TENANT", DEFAULT_TENANT)
    domain = domain_for_tenant(tenant)
    # sofia_contact uses the live registration. user/ext needs a directory
    # dial-string and fails with MANDATORY_IE_MISSING when that cache is stale.
    return FsCommand(
        "uuid_transfer",
        f"{uuid} bridge:${{sofia_contact({ext}@{domain})}} inline",
    )


def _bridge_extension_commands(uuid: str, ext: str) -> list[FsCommand]:
    # Do not pre-answer. A 183 before the callee answers makes the caller
    # CANCEL the INVITE while the other browser is still sending 200 OK.
    return [_extension_transfer(uuid, ext)]


def _commands_for_entry(
    ctx: EngineCtx, entry: EntryView, uuid: str, call_id: str
) -> list[FsCommand]:
    if entry.kind == "ivr":
        return _enter_ivr(ctx, entry.target, uuid, call_id)
    if entry.kind == "extension":
        return _bridge_extension_commands(uuid, entry.target)
    if entry.kind == "queue":
        return [
            FsCommand("uuid_answer", uuid),
            FsCommand("uuid_setvar", f"{uuid} vr_skill_group={entry.target}"),
        ]
    if entry.kind == "playback":
        ctx.pending_active[call_id] = [_broadcast(uuid, entry.target)]
        return [FsCommand("uuid_answer", uuid)]
    if entry.kind == "robot":
        ctx.pending_active[call_id] = robot_stream_commands(uuid, call_id, entry.target)
        return [FsCommand("uuid_answer", uuid)]
    return []


def _handle_dtmf(ctx: EngineCtx, event: dict[str, Any], call_id: str, uuid: str) -> list[FsCommand]:
    session = ctx.sessions.get(call_id)
    if session is None or not session.state.startswith("ivr:") or not session.entry.startswith("ivr:"):
        return []
    flow_key = session.entry.split(":", 1)[1]
    loaded = _load_ivr(ctx, flow_key)
    if loaded is None:
        return []
    _version, snapshot = loaded
    node_id = session.state.split(":", 1)[1]
    digit = event.get("DTMF-Digit")
    buffer = ctx.ivr_buffers.get(call_id, "")
    result = ivr_step(snapshot, node_id, digit, buffer=buffer)
    if result.get("action") == "noop" and digit and digit not in {"*", "#"}:
        ctx.ivr_buffers[call_id] = buffer + str(digit)
        return []
    if result.get("action") == "complete":
        ctx.ivr_buffers.pop(call_id, None)
    next_node, cmds = _ivr_follow(ctx, uuid, call_id, snapshot, result, node_id)
    ctx.sessions[call_id] = CallSession(
        call_id=session.call_id,
        fs_node=session.fs_node,
        tenant_id=session.tenant_id,
        entry=session.entry,
        state=f"ivr:{next_node}",
        ivr_version=session.ivr_version,
    )
    return cmds


def handle_event(event: dict[str, Any], ctx: EngineCtx) -> list[FsCommand]:
    """Route one ESL event through shared decisions; append cmds to ``ctx.sent``."""
    event_name = event.get("Event-Name", "")
    decision = offer(ctx.queue_size, ctx.capacity, event_name)
    if decision == "drop":
        return []

    call_id = _call_key(event)
    uuid = event.get("Unique-ID", "")
    cmds: list[FsCommand] = []

    subclass = str(event.get("Event-Subclass") or "")
    if event_name == "CUSTOM" and subclass.startswith("sofia::"):
        try:
            ext = extension_from_register_event(event)
            contact = str(event.get("contact") or "")
            logger.info("sofia event=%s ext=%s contact=%s", subclass, ext or "-", contact or "-")
            if ext and subclass == "sofia::register":
                mark_registered(ext, contact)
            elif ext and subclass in {"sofia::unregister", "sofia::expire"}:
                mark_unregistered(ext)
        except Exception:
            logger.exception("sofia registration update failed subclass=%s", subclass)
    elif event_name == "CUSTOM" and subclass == "mod_audio_stream::play":
        cmds = _stream_play_commands(event, uuid)
    elif event_name == "CHANNEL_PARK":
        existing = ctx.sessions.get(call_id)
        if existing is None:
            dest = event.get("Caller-Destination-Number", "")
            entry, resolve_reason = _explain_park_entry(event, ctx, dest)
            if dest in ctx.numbers:
                number_status = ctx.numbers[dest]
            elif entry is not None:
                number_status = "enabled"
            else:
                number_status = "disabled"
            direction = event.get("Caller-Direction", "")
            cmds = decide_park(direction, entry, number_status, uuid)
            explicit_entry = bool(event.get("variable_vr_entry"))
            if (
                not cmds
                and entry is not None
                and number_status != "disabled"
                and (direction == "inbound" or explicit_entry)
            ):
                cmds = _commands_for_entry(ctx, entry, uuid, call_id)
            raw_entry = event.get("variable_vr_entry") or ""
            reason = park_decision_reason(raw_entry or None, entry, number_status, cmds)
            logger.info(
                "channel park uuid=%s dest=%s direction=%s vr_entry=%s resolved=%s reason=%s detail=%s cmds=%s",
                uuid or "-",
                dest or "-",
                direction or "-",
                raw_entry or "-",
                f"{entry.kind}:{entry.target}" if entry is not None else "-",
                reason,
                resolve_reason,
                " | ".join(f"{cmd.api} {cmd.args}" for cmd in cmds) or "-",
            )
            if reason == "vr-entry-unparsed":
                logger.warning(
                    "channel park rejected uuid=%s reason=vr-entry-unparsed detail=%s raw=%s",
                    uuid or "-",
                    resolve_reason,
                    raw_entry,
                )
        else:
            logger.info("channel park uuid=%s call_id=%s skipped=already-handled", uuid or "-", call_id or "-")
    elif event_name == "CHANNEL_CALLSTATE":
        # WebRTC 不渲染 EARLY 阶段的 early media，提示音必须等 ACTIVE
        if event.get("Channel-Call-State") == "ACTIVE":
            cmds = _flush_pending_active(ctx, call_id)
    elif event_name == "DTMF":
        cmds = _handle_dtmf(ctx, event, call_id, uuid)
    elif event_name == "HEARTBEAT":
        cmds = run_maintenance(ctx)
    elif event_name == "VR_BRIDGE":
        cmds = bridge(str(event["a_uuid"]), str(event["b_uuid"]))
    elif event_name == "VR_CANCEL":
        cmds = commands_for_cancel(uuid)
    elif event_name == "VR_AI_CMD":
        cmds = _ai_command(ctx, event, uuid)
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

    if (
        call_id
        and _is_primary_cdr_leg(event)
        and event_name
        not in {
            "VR_BRIDGE",
            "VR_CANCEL",
            "VR_DIAL",
            "VR_RECONCILE",
            "VR_RELEASE_APPLY",
            "HEARTBEAT",
            "DTMF",
            "CUSTOM",
            "CHANNEL_CALLSTATE",
            "VR_AI_CMD",
        }
    ):
        row = ctx.cdr_rows.get(call_id, {})
        updated = apply_event(row, _cdr_payload(event, call_id))
        ctx.cdr_rows[call_id] = updated
        _xadd_cdr(ctx, updated)
        if event_name == "CHANNEL_HANGUP_COMPLETE":
            if call_id:
                ctx.pending_active.pop(call_id, None)
                ctx.ivr_buffers.pop(call_id, None)
            if ctx.db is not None:
                try:
                    upsert_call_record(ctx.db, updated)
                    logger.info(
                        "cdr saved call_id=%s caller=%s callee=%s billsec=%s cause=%s",
                        call_id,
                        updated.get("caller") or "-",
                        updated.get("callee") or "-",
                        updated.get("billsec", "-"),
                        updated.get("hangup_cause") or "-",
                    )
                except Exception:
                    logger.exception("cdr save failed call_id=%s", call_id)
                    ctx.db.rollback()

    try:
        apply_verify_event(event, cmds)
    except Exception:
        pass

    return cmds
