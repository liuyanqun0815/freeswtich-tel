"""Tencent Cloud real-time speech recognition over WebSocket (asr.cloud.tencent.com/asr/v2)."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import time
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from websockets.asyncio.client import connect as ws_connect

from vr_ai.providers.base import AsrSession, ProviderError

logger = logging.getLogger("vr.ai.asr.tencent")

_HOST = "asr.cloud.tencent.com"
_SIGN_TTL_SECONDS = 86400


def signed_url(
    app_id: str,
    secret_id: str,
    secret_key: str,
    params: dict[str, Any],
    *,
    timestamp: int,
    nonce: int,
    voice_id: str,
) -> str:
    query = {
        **params,
        "secretid": secret_id,
        "timestamp": timestamp,
        "expired": timestamp + _SIGN_TTL_SECONDS,
        "nonce": nonce,
        "voice_id": voice_id,
    }
    plain = f"{_HOST}/asr/v2/{app_id}?" + "&".join(f"{key}={query[key]}" for key in sorted(query))
    digest = hmac.new(secret_key.encode("utf-8"), plain.encode("utf-8"), hashlib.sha1).digest()
    return f"wss://{plain}&signature={quote(base64.b64encode(digest).decode('ascii'))}"


def _check(raw: str | bytes) -> dict[str, Any]:
    message = json.loads(raw)
    if not isinstance(message, dict):
        raise ProviderError(f"tencent asr bad message: {str(raw)[:200]}")
    code = message.get("code", 0)
    if code != 0:
        raise ProviderError(f"tencent asr error {code}: {message.get('message') or '-'}")
    return message


def _ignore_outcome(task: asyncio.Task) -> None:
    # 被打断而未 finish 的会话由服务端空闲超时关掉，其异常不需要再上报
    if not task.cancelled():
        task.exception()


class _StreamingSession:
    def __init__(self, url: str, connect: Callable[[str], Any], timeout: float) -> None:
        self._audio: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._timeout = timeout
        self._task = asyncio.get_running_loop().create_task(self._recognize(url, connect))
        self._task.add_done_callback(_ignore_outcome)

    def feed(self, pcm: bytes) -> None:
        self._audio.put_nowait(pcm)

    async def finish(self) -> str:
        self._audio.put_nowait(None)
        started = time.monotonic()
        try:
            text = await asyncio.wait_for(self._task, self._timeout)
        except TimeoutError as exc:
            logger.error("asr timeout after %.0fms", (time.monotonic() - started) * 1000)
            raise ProviderError("tencent asr timed out waiting for the final result") from exc
        logger.info(
            "asr finish ms=%.0f chars=%s text=%r",
            (time.monotonic() - started) * 1000,
            len(text),
            (text[:160] + "…") if len(text) > 160 else text,
        )
        return text

    async def _recognize(self, url: str, connect: Callable[[str], Any]) -> str:
        texts: dict[int, str] = {}
        logger.info("asr connect engine=%s", self._url_engine_hint(url))
        try:
            async with connect(url) as ws:
                sender = asyncio.create_task(self._send(ws))
                sender.add_done_callback(_ignore_outcome)
                try:
                    async for raw in ws:
                        message = _check(raw)
                        result = message.get("result")
                        if isinstance(result, dict):
                            texts[int(result.get("index") or 0)] = str(result.get("voice_text_str") or "")
                        if message.get("final") == 1:
                            break
                    else:
                        raise ProviderError("tencent asr closed before the final result")
                finally:
                    sender.cancel()
        except ProviderError:
            raise
        except Exception as exc:
            logger.exception("asr websocket failed")
            raise ProviderError(f"tencent asr failed: {exc}") from exc
        return "".join(texts[index] for index in sorted(texts)).strip()

    @staticmethod
    def _url_engine_hint(url: str) -> str:
        marker = "engine_model_type="
        if marker not in url:
            return "-"
        return url.split(marker, 1)[1].split("&", 1)[0]

    async def _send(self, ws: Any) -> None:
        while (chunk := await self._audio.get()) is not None:
            await ws.send(chunk)
        await ws.send(json.dumps({"type": "end"}))


def _credential(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProviderError(f"environment variable {name} is empty")
    return value


class TencentAsr:
    """Credentials come from .env (TENCENT_APP_ID / TENCENT_SECRET_ID / TENCENT_SECRET_KEY), not the scene."""

    def __init__(self, config: dict[str, Any], connect: Callable[[str], Any] | None = None) -> None:
        self._app_id = _credential("TENCENT_APP_ID")
        if not self._app_id.isdigit():
            raise ProviderError("environment variable TENCENT_APP_ID must be the numeric AppId")
        self._secret_id = _credential("TENCENT_SECRET_ID")
        self._secret_key = _credential("TENCENT_SECRET_KEY")
        self._params = {
            "engine_model_type": str(config.get("engine_model_type") or "8k_zh"),
            "voice_format": 1,
            "needvad": 0,
        }
        self._timeout = float(config.get("timeout") or 10)
        self._connect = connect or ws_connect

    def open(self) -> AsrSession:
        url = signed_url(
            self._app_id,
            self._secret_id,
            self._secret_key,
            self._params,
            timestamp=int(time.time()),
            nonce=random.randint(1, 9_999_999_999),
            voice_id=uuid.uuid4().hex,
        )
        return _StreamingSession(url, self._connect, self._timeout)
