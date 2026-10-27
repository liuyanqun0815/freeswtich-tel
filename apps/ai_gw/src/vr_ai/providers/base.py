"""Vendor-neutral speech recognition, speech synthesis and dialog interfaces."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class ProviderError(RuntimeError):
    """A vendor call failed; the session falls back per scene config."""


class AsrSession(Protocol):
    def feed(self, pcm: bytes) -> None: ...

    async def finish(self) -> str: ...


class Asr(Protocol):
    def open(self) -> AsrSession: ...


class Tts(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]: ...


class Llm(Protocol):
    def stream(self, messages: list[dict]) -> AsyncIterator[dict]: ...
