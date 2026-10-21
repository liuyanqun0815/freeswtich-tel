from __future__ import annotations

_ASSISTANT_PREFIX = "我是智能助理。"


def opening_text(scene_opening: str) -> str:
    return f"{_ASSISTANT_PREFIX}{scene_opening}"


class ScriptBot:
    def __init__(self, keywords: list[str] | None = None) -> None:
        self._keywords = keywords or []

    def reply(self, final_text: str) -> dict:
        if any(keyword in final_text for keyword in self._keywords):
            return {"type": "action.transfer"}
        return {"type": "response.text", "text": final_text}
