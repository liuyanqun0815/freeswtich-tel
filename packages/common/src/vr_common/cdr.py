"""Fold call stream events into idempotent CDR rows and query helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vr_common.db import DEFAULT_TENANT
from vr_common.models import CallRecord

CALL_RECORD_FIELDS = frozenset(
    {
        "vr_call_id",
        "tenant_id",
        "caller",
        "callee",
        "direction",
        "agent_ext",
        "started_at",
        "answered_at",
        "ended_at",
        "duration",
        "billsec",
        "queue_sec",
        "robot_sec",
        "agent_billsec",
        "hangup_cause",
        "record_url",
        "restricted",
        "restrict_reason",
    }
)

_OVERWRITE_ON_REPEAT = frozenset(
    {
        "duration",
        "billsec",
        "queue_sec",
        "robot_sec",
        "agent_billsec",
        "ended_at",
    }
)


def apply_event(row: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Merge one stream event into a CDR row dict (idempotent for hangup)."""
    result = dict(row)
    event_type = event.get("type")

    for key, value in event.items():
        if key == "type" or key not in CALL_RECORD_FIELDS:
            continue
        if key == "hangup_cause":
            continue
        if key in _OVERWRITE_ON_REPEAT or key not in result or result[key] is None:
            result[key] = value

    if event_type == "answer" and result.get("answered_at") is None:
        result["answered_at"] = event.get("answered_at") or event.get("event_at")

    if event_type == "hangup":
        cause = event.get("hangup_cause")
        if cause and not result.get("hangup_cause"):
            result["hangup_cause"] = cause

    if "tenant_id" not in result:
        result["tenant_id"] = DEFAULT_TENANT

    return result


def upsert_call_record(db: Session, row: dict[str, Any]) -> None:
    """Insert or update the CDR row the admin list reads."""
    call_id = str(row.get("vr_call_id") or "")
    if not call_id:
        return
    record = db.scalars(select(CallRecord).where(CallRecord.vr_call_id == call_id)).first()
    if record is None:
        record = CallRecord(
            vr_call_id=call_id,
            tenant_id=str(row.get("tenant_id") or DEFAULT_TENANT),
        )
        db.add(record)
    for key in CALL_RECORD_FIELDS:
        if key in {"vr_call_id", "tenant_id"}:
            continue
        if key not in row or row[key] is None or row[key] == "":
            continue
        setattr(record, key, row[key])
    db.commit()


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def call_record_to_item(record: CallRecord) -> dict[str, Any]:
    """Serialize a CallRecord for the CDR list API (no fee fields)."""
    return {
        "vr_call_id": record.vr_call_id,
        "caller": record.caller or "",
        "callee": record.callee or "",
        "direction": record.direction or "",
        "started_at": _iso(record.started_at),
        "answered_at": _iso(record.answered_at),
        "ended_at": _iso(record.ended_at),
        "billsec": record.billsec or 0,
        "duration": record.duration or 0,
        "agent_billsec": record.agent_billsec or 0,
        "queue_sec": record.queue_sec or 0,
        "robot_sec": record.robot_sec or 0,
        "hangup_cause": record.hangup_cause or "",
    }


def build_cdr_summary(records: list[CallRecord], *, fee_display: str = "—") -> dict[str, Any]:
    total = len(records)
    answered = sum(1 for r in records if r.answered_at is not None)
    restricted = sum(1 for r in records if r.restricted)
    talk_sec = sum(r.billsec or 0 for r in records)
    return {
        "fee_display": fee_display,
        "answer_rate": answered / total if total else 0,
        "restricted_rate": restricted / total if total else 0,
        "answered": answered,
        "total": total,
        "talk_sec": talk_sec,
    }


def parse_instant(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)
