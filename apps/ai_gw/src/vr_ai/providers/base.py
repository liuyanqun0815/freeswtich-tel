"""Vendor-neutral speech recognition, speech synthesis and dialog interfaces."""

from __future__ import annotations

import os
import re
from collections.abc import AsyncIterator
from typing import Any, Protocol

# 只允许这类变量名，防止配置把 JWT_SECRET 等其他 .env 值当凭证发给外部地址
SECRET_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*_API_KEY$")


class ProviderError(RuntimeError):
    """A vendor call failed; the session falls back per scene config."""


def read_secret(config: dict[str, Any], field: str, *, required: bool) -> str:
    """Read the credential whose environment variable name is stored in ``config[field]``."""
    name = str(config.get(field) or "")
    if not name:
        if required:
            raise ProviderError(f"{field} is required")
        return ""
    if not SECRET_ENV_NAME.match(name):
        raise ProviderError(f"{field} {name} must match {SECRET_ENV_NAME.pattern}")
    value = os.environ.get(name, "")
    if not value:
        raise ProviderError(f"environment variable {name} is empty")
    return value


class AsrSession(Protocol):
    def feed(self, pcm: bytes) -> None: ...

    async def finish(self) -> str: ...


class Asr(Protocol):
    def open(self) -> AsrSession: ...


class Tts(Protocol):
    def stream(self, text: str) -> AsyncIterator[bytes]: ...


class Llm(Protocol):
    def stream(self, messages: list[dict]) -> AsyncIterator[dict]: ...
