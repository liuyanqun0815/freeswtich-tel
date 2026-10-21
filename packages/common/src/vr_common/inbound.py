import json
from dataclasses import dataclass

from vr_common.commands import FsCommand
from vr_common.session import CallSession

_BUSY_PROMPT = "ivr/vr_busy.wav"
_RECORDING_PATH = "/var/lib/vr/recordings/${strftime(%Y%m%d)}/{call_id}.wav"
_AI_STREAM_URL = "ws://ai-gw:8040/fs/stream"


@dataclass(frozen=True, slots=True)
class EntryView:
    kind: str
    target: str


def _reject_call(uuid: str) -> list[FsCommand]:
    return [
        FsCommand("uuid_broadcast", f"{uuid} {_BUSY_PROMPT} aleg"),
        FsCommand("uuid_kill", uuid),
    ]


def _robot_commands(uuid: str, call_id: str, *, answer: bool) -> list[FsCommand]:
    record_path = _RECORDING_PATH.replace("{call_id}", call_id)
    metadata = json.dumps({"call_id": call_id}, separators=(",", ":"))
    cmds: list[FsCommand] = []
    if answer:
        cmds.append(FsCommand("uuid_answer", uuid))
    cmds.extend(
        [
            FsCommand("uuid_setvar", f"{uuid} RECORD_STEREO=true"),
            FsCommand("uuid_record", f"{uuid} start {record_path}"),
            FsCommand(
                "uuid_audio_stream",
                f"{uuid} start {_AI_STREAM_URL} mono 8k {metadata}",
            ),
        ]
    )
    return cmds


def decide_park(
    existing: CallSession | None,
    direction: str,
    entry: EntryView | None,
    number_status: str,
    uuid: str,
    call_id: str,
) -> list[FsCommand]:
    if direction != "inbound":
        return []
    if entry is None or number_status == "disabled":
        return _reject_call(uuid)
    if entry.kind == "robot":
        return _robot_commands(uuid, call_id, answer=existing is None)
    return []
