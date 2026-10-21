from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FsCommand:
    api: str
    args: str
