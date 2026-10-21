from __future__ import annotations

from vr_common.commands import FsCommand

AGENT_STATES = frozenset({"offline", "idle", "ringing", "talking", "wrapup", "break"})

AGENT_WS_MESSAGE_TYPES = frozenset(
    {
        "agent.ready",
        "agent.not_ready",
        "agent.wrapup_done",
        "call.hold",
        "call.unhold",
        "call.transfer",
        "call.hangup",
        "ping",
    }
)


def can_take_call(sip_registered: bool, ws_online: bool, state: str) -> bool:
    return sip_registered and ws_online and state == "idle"


def on_ws_silent(silent_seconds: int, state: str) -> str:
    if silent_seconds >= 60 and state == "idle":
        return "break"
    return state


def pop_before_dial(summary: str, call_id: str) -> dict:
    return {
        "type": "call.incoming",
        "call_id": call_id,
        "summary": summary,
    }


def state_for_agent_message(msg_type: str) -> str | None:
    return {
        "agent.ready": "idle",
        "agent.not_ready": "break",
        "agent.wrapup_done": "idle",
    }.get(msg_type)


def dial_agent_after_pop(ext: str, call_id: str) -> list[FsCommand]:
    vars_part = f"{{vr_call_id={call_id}}}"
    return [
        FsCommand("global_setvar", f"vr_agent_pop_{call_id}={ext}"),
        FsCommand("originate", f"{vars_part}user/{ext} &park()"),
    ]
