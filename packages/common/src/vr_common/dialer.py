from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class TaskWindow:
    morning: tuple[int, int] = (9, 12)
    afternoon: tuple[int, int] = (14, 20)
    max_success: int = 1


def task_from_dial_task(window_json: str, max_success_per_number_per_day: int) -> TaskWindow:
    raw = json.loads(window_json)
    if not raw:
        return TaskWindow(max_success=max_success_per_number_per_day)
    morning = (int(raw[0][0]), int(raw[0][1]))
    afternoon = (int(raw[1][0]), int(raw[1][1])) if len(raw) > 1 else (14, 20)
    return TaskWindow(
        morning=morning,
        afternoon=afternoon,
        max_success=max_success_per_number_per_day,
    )


def _in_hour_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    return start <= hour <= end


def _inside_call_window(now: datetime, task: TaskWindow) -> bool:
    hour = now.hour
    return _in_hour_window(hour, task.morning) or _in_hour_window(hour, task.afternoon)


def _blacklist_phones(blacklist: Iterable[str] | Iterable[object]) -> set[str]:
    phones: set[str] = set()
    for item in blacklist:
        if isinstance(item, str):
            phones.add(item)
        else:
            phone = getattr(item, "phone", None)
            if phone is not None:
                phones.add(str(phone))
    return phones


def block_reason(
    now: datetime,
    task: TaskWindow,
    number: str,
    blacklist: Iterable[str] | Iterable[object],
    success_count_24h: int,
) -> str | None:
    if number in _blacklist_phones(blacklist):
        return "blacklist"
    if not _inside_call_window(now, task):
        return "outside_window"
    if success_count_24h >= task.max_success:
        return "frequency"
    return None


def originate(gateway: str, caller: str, callee: str, call_id: str, uuid: str) -> str:
    vars_part = (
        f"{{origination_uuid={uuid},vr_call_id={call_id},"
        f"origination_caller_id_number={caller}}}"
    )
    return f"originate {vars_part}sofia/gateway/{gateway}/{callee} &park()"


def next_action(
    now: datetime,
    task: TaskWindow,
    number: str,
    blacklist: Iterable[str] | Iterable[object],
    success_count_24h: int,
    *,
    gateway: str,
    caller: str,
    call_id: str,
    uuid: str,
) -> dict:
    reason = block_reason(now, task, number, blacklist, success_count_24h)
    if reason is not None:
        return {"status": "blocked", "reason": reason}
    return {
        "status": "originate",
        "originate": originate(gateway, caller, number, call_id, uuid),
    }
