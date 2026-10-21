from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from vr_common.commands import FsCommand

_KNOWN_TYPES = frozenset(
    {"session.start", "response.cancel", "action.transfer", "action.hangup"}
)
_PHONE_SAMPLE_RATE = 8000


class _MessageBase(BaseModel):
    type: str
    session_id: str
    seq: int
    ts: int


class SessionStartMessage(_MessageBase):
    type: Literal["session.start"]
    sample_rate: int = Field(...)


class ResponseCancelMessage(_MessageBase):
    type: Literal["response.cancel"]


class ActionTransferMessage(_MessageBase):
    type: Literal["action.transfer"]


class ActionHangupMessage(_MessageBase):
    type: Literal["action.hangup"]


_MESSAGE_MODELS: dict[str, type[BaseModel]] = {
    "session.start": SessionStartMessage,
    "response.cancel": ResponseCancelMessage,
    "action.transfer": ActionTransferMessage,
    "action.hangup": ActionHangupMessage,
}


def parse_message(raw: str) -> dict[str, Any] | None:
    data = json.loads(raw)
    msg_type = data.get("type")
    if msg_type not in _KNOWN_TYPES:
        return None
    if msg_type == "session.start" and data.get("sample_rate") != _PHONE_SAMPLE_RATE:
        raise ValueError("sample_rate must be 8000")
    model = _MESSAGE_MODELS[msg_type].model_validate(data)
    return model.model_dump()


def degraded(elapsed_ms: int, saw_response: bool) -> bool:
    return elapsed_ms >= 3000 and not saw_response


def reject_keyword_hit(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def commands_for_cancel(uuid: str) -> list[FsCommand]:
    return [FsCommand("uuid_break", f"{uuid} all")]


def commands_for_transfer(uuid: str) -> list[FsCommand]:
    return [FsCommand("uuid_audio_stream", f"{uuid} stop")]
