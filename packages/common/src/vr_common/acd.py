from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vr_common.agent import can_take_call


@dataclass(frozen=True)
class AgentSnapshot:
    agent_id: str
    idle_since: datetime
    sip_registered: bool
    ws_online: bool
    state: str


def pick_longest_idle(agents: list[AgentSnapshot]) -> str | None:
    eligible = [
        a
        for a in agents
        if can_take_call(a.sip_registered, a.ws_online, a.state)
    ]
    if not eligible:
        return None
    return min(eligible, key=lambda a: a.idle_since).agent_id


def on_ring_timeout(agent_id: str, waited: int, limit: int) -> str:
    return "requeue" if waited >= limit else "keep"


def overflow(reason: str, target: str) -> str:
    return target
