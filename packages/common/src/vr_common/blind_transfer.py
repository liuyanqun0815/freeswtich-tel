"""Pick the FreeSWITCH channel leg for an agent extension during blind transfer."""

from __future__ import annotations

import json
from typing import Any


def _field(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def pick_extension_leg_uuid(channels_payload: str, extension: str) -> str | None:
    """Return the UUID of an answered/active leg that belongs to ``extension``.

    Accepts the body of ``show channels as json``. Prefers the newest matching row.
    """
    ext = (extension or "").strip()
    if not ext:
        return None
    try:
        payload = json.loads(channels_payload or "")
    except json.JSONDecodeError:
        return None
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    matches: list[tuple[int, str]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        uuid = _field(row, "uuid", "unique-id", "Unique-ID")
        if not uuid:
            continue
        cid = _field(row, "cid_num", "caller_id_number", "Caller-Caller-ID-Number")
        dest = _field(row, "dest", "callee_num", "Caller-Destination-Number", "Caller-Callee-ID-Number")
        presence = _field(row, "presence_id", "Presence-ID", "call_uuid")
        presence_user = presence.split("@", 1)[0] if presence else ""
        if ext not in {cid, dest, presence_user}:
            continue
        callstate = _field(row, "callstate", "Call-State", "state").upper()
        # Prefer answered legs; allow empty callstate when FS omits it in JSON.
        if callstate and callstate not in {"ACTIVE", "HELD"}:
            continue
        matches.append((index, uuid))
    if not matches:
        return None
    return matches[-1][1]
