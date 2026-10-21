from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FsCommand:
    api: str
    args: str


def bridge(a_uuid: str, b_uuid: str) -> list[FsCommand]:
    return [
        FsCommand("uuid_setvar", f"{a_uuid} park_after_bridge=true"),
        FsCommand("uuid_setvar", f"{b_uuid} park_after_bridge=true"),
        FsCommand("uuid_bridge", f"{a_uuid} {b_uuid}"),
    ]


def on_leg_hangup(other_uuid: str, park_after_bridge: bool) -> list[FsCommand]:
    if park_after_bridge:
        return []
    return [FsCommand("uuid_kill", other_uuid)]


def stop_stream(uuid: str) -> FsCommand:
    return FsCommand("uuid_audio_stream", f"{uuid} stop")


def finish_if_no_next(uuid: str) -> FsCommand:
    return FsCommand("uuid_kill", uuid)
