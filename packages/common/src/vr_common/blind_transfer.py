"""Pick FreeSWITCH channel legs for agent blind transfer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


def _field(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _rows(channels_payload: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(channels_payload or "")
    except json.JSONDecodeError:
        return []
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _extension_score(row: dict[str, Any], ext: str) -> int:
    """Rank how strongly a channel belongs to ``ext``.

    Prefer presence_id / cid_num. Do not treat dest alone as ownership — the
    caller's inbound leg often has dest=<agent>, which would steal the match.
    """
    presence = _field(row, "presence_id", "Presence-ID")
    presence_user = presence.split("@", 1)[0] if presence else ""
    cid = _field(row, "cid_num", "caller_id_number", "Caller-Caller-ID-Number")
    if presence_user == ext:
        return 3
    if cid == ext:
        return 2
    return 0


def pick_extension_leg_uuid(channels_payload: str, extension: str) -> str | None:
    """Return the UUID of an answered/active leg that belongs to ``extension``.

    Accepts the body of ``show channels as json``. Prefers presence_id, then
    cid_num; among ties, the newest matching row.
    """
    ext = (extension or "").strip()
    if not ext:
        return None
    matches: list[tuple[int, int, str]] = []
    for index, row in enumerate(_rows(channels_payload)):
        uuid = _field(row, "uuid", "unique-id", "Unique-ID")
        if not uuid:
            continue
        score = _extension_score(row, ext)
        if score <= 0:
            continue
        callstate = _field(row, "callstate", "Call-State", "state").upper()
        # Prefer answered legs; allow empty callstate when FS omits it in JSON.
        if callstate and callstate not in {"ACTIVE", "HELD"}:
            continue
        matches.append((score, index, uuid))
    if not matches:
        return None
    matches.sort()
    return matches[-1][2]


def pick_bridged_peer_uuid(channels_payload: str, leg_uuid: str) -> str | None:
    """Return the other active leg that shares ``call_uuid`` with ``leg_uuid``."""
    target = (leg_uuid or "").strip()
    if not target:
        return None
    rows = _rows(channels_payload)
    call_uuid = ""
    for row in rows:
        if _field(row, "uuid", "unique-id", "Unique-ID") == target:
            call_uuid = _field(row, "call_uuid", "Call-UUID", "bridge_uuid", "Bridge-UUID")
            break
    if not call_uuid:
        return None
    for row in rows:
        uuid = _field(row, "uuid", "unique-id", "Unique-ID")
        if not uuid or uuid == target:
            continue
        peer_call = _field(row, "call_uuid", "Call-UUID", "bridge_uuid", "Bridge-UUID")
        if peer_call != call_uuid:
            continue
        callstate = _field(row, "callstate", "Call-State", "state").upper()
        if callstate and callstate not in {"ACTIVE", "HELD"}:
            continue
        return uuid
    return None


@dataclass(frozen=True)
class BlindTransferTarget:
    """Channel to ``uuid_transfer``; ``use_bleg`` transfers the far side of the bridge."""

    uuid: str
    use_bleg: bool


def resolve_blind_transfer_target(channels_payload: str, extension: str) -> BlindTransferTarget | None:
    """Pick the caller/peer leg to redirect; fall back to agent uuid + ``-bleg``."""
    agent_uuid = pick_extension_leg_uuid(channels_payload, extension)
    if not agent_uuid:
        return None
    peer = pick_bridged_peer_uuid(channels_payload, agent_uuid)
    if peer:
        return BlindTransferTarget(uuid=peer, use_bleg=False)
    return BlindTransferTarget(uuid=agent_uuid, use_bleg=True)
