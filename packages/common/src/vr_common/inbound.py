import json
import os
from dataclasses import dataclass

from vr_common.commands import FsCommand

_BUSY_PROMPT = "ivr/vr_busy.wav"
_RECORDING_PATH = "/var/lib/vr/recordings/${strftime(%Y%m%d)}/{call_id}.wav"
_DEFAULT_AI_GW_WS_URL = "ws://127.0.0.1:8040/fs/stream"


@dataclass(frozen=True, slots=True)
class EntryView:
    kind: str
    target: str


def _reject_call(uuid: str) -> list[FsCommand]:
    return [
        FsCommand("uuid_broadcast", f"{uuid} {_BUSY_PROMPT} aleg"),
        FsCommand("uuid_kill", uuid),
    ]


def robot_stream_commands(uuid: str, call_id: str, scene_id: str) -> list[FsCommand]:
    record_path = _RECORDING_PATH.replace("{call_id}", call_id)
    url = os.environ.get("AI_GW_WS_URL", _DEFAULT_AI_GW_WS_URL)
    metadata = json.dumps({"call_id": call_id, "uuid": uuid, "scene_id": scene_id}, separators=(",", ":"))
    return [
        FsCommand("uuid_setvar", f"{uuid} RECORD_STEREO=true"),
        FsCommand("uuid_record", f"{uuid} start {record_path}"),
        FsCommand("uuid_audio_stream", f"{uuid} start {url} mono 8k {metadata}"),
    ]


def decide_park(direction: str, entry: EntryView | None, number_status: str, uuid: str) -> list[FsCommand]:
    if direction != "inbound":
        return []
    if entry is None or number_status == "disabled":
        return _reject_call(uuid)
    return []
