"""Map between FreeSWITCH PCM / unified protocol and voice-robot wire format."""

from __future__ import annotations

import base64
from typing import Any

_TRANSFER_MARKER = "[[transfer]]"


def to_voice_robot(pcm: bytes, sample_rate: int) -> dict[str, Any]:
    return {
        "type": "audio_chunk",
        "audio": base64.b64encode(pcm).decode("ascii"),
        "sample_rate": sample_rate,
    }


def from_voice_robot(msg: dict) -> dict[str, Any] | None:
    if not isinstance(msg, dict):
        return None
    text = msg.get("text")
    if isinstance(text, str) and _TRANSFER_MARKER in text:
        cleaned = text.replace(_TRANSFER_MARKER, "")
        out: dict[str, Any] = {"type": "action.transfer", "text": cleaned}
        for key in ("session_id", "seq", "ts"):
            if key in msg:
                out[key] = msg[key]
        return out
    return None
