"""Volcengine bidirectional streaming TTS (wss://openspeech.bytedance.com/api/v3/tts/bidirection)."""

from __future__ import annotations

import gzip
import json
import logging
import os
import struct
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.client import connect as ws_connect

from vr_ai.audio import PHONE_RATE
from vr_ai.providers.base import ProviderError

logger = logging.getLogger("vr.ai.tts.volc")

_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/tts/bidirection"

_FULL_CLIENT = 0b0001
_FULL_SERVER = 0b1001
_AUDIO_SERVER = 0b1011
_FRONT_END_SERVER = 0b1100
_ERROR = 0b1111
_WITH_EVENT = 0b100
_SEQUENCE_FLAGS = (0b001, 0b011)

_START_CONNECTION = 1
_FINISH_CONNECTION = 2
_CONNECTION_STARTED = 50
_CONNECTION_FAILED = 51
_CONNECTION_FINISHED = 52
_START_SESSION = 100
_FINISH_SESSION = 102
_SESSION_STARTED = 150
_SESSION_FINISHED = 152
_SESSION_FAILED = 153
_TASK_REQUEST = 200

# 连接级事件的帧里没有 session_id；连接下行事件带 connect_id
_CONNECTION_EVENTS = {
    _START_CONNECTION,
    _FINISH_CONNECTION,
    _CONNECTION_STARTED,
    _CONNECTION_FAILED,
    _CONNECTION_FINISHED,
}
_CONNECT_ID_EVENTS = {_CONNECTION_STARTED, _CONNECTION_FAILED, _CONNECTION_FINISHED}


@dataclass
class _Message:
    msg_type: int
    event: int = 0
    error_code: int = 0
    payload: bytes = b""


def _sized(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def _client_frame(event: int, payload: bytes, session_id: str = "") -> bytes:
    # 版本 1 / 4 字节头；FullClientRequest + 带事件；JSON、不压缩
    frame = bytes([0x11, (_FULL_CLIENT << 4) | _WITH_EVENT, 0x10, 0x00]) + struct.pack(">i", event)
    if event not in _CONNECTION_EVENTS:
        frame += _sized(session_id.encode("utf-8"))
    return frame + _sized(payload)


def _parse(data: bytes | str) -> _Message:
    if isinstance(data, str):
        raise ProviderError(f"volcengine tts unexpected text frame: {data[:200]}")
    msg_type, flag = data[1] >> 4, data[1] & 0x0F
    compression = data[2] & 0x0F
    pos = (data[0] & 0x0F) * 4

    def take(size: int) -> bytes:
        nonlocal pos
        chunk = data[pos : pos + size]
        pos += size
        return chunk

    def take_sized() -> bytes:
        return take(struct.unpack(">I", take(4))[0])

    message = _Message(msg_type=msg_type)
    if msg_type == _ERROR:
        message.error_code = struct.unpack(">I", take(4))[0]
    elif flag in _SEQUENCE_FLAGS:
        take(4)
    if flag == _WITH_EVENT:
        message.event = struct.unpack(">i", take(4))[0]
        if message.event not in _CONNECTION_EVENTS:
            take_sized()
        if message.event in _CONNECT_ID_EVENTS:
            take_sized()
    payload = take_sized()
    message.payload = gzip.decompress(payload) if compression == 1 and payload else payload

    if msg_type == _ERROR:
        detail = message.payload.decode("utf-8", errors="replace")[:300]
        raise ProviderError(f"volcengine tts error {message.error_code}: {detail}")
    if message.event in (_CONNECTION_FAILED, _SESSION_FAILED):
        detail = message.payload.decode("utf-8", errors="replace")[:300]
        raise ProviderError(f"volcengine tts event {message.event} failed: {detail}")
    return message


async def _expect(ws: Any, event: int) -> None:
    message = _parse(await ws.recv())
    if message.event != event:
        raise ProviderError(f"volcengine tts expected event {event}, got {message.event}")


def _credential(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProviderError(f"environment variable {name} is empty")
    return value


def _default_connect(url: str, headers: dict[str, str]) -> Any:
    return ws_connect(url, additional_headers=headers, max_size=10 * 1024 * 1024)


class VolcTts:
    """Credentials come from .env (VOLC_APP_ID / VOLC_ACCESS_TOKEN); the scene only picks the voice."""

    def __init__(
        self,
        config: dict[str, Any],
        connect: Callable[[str, dict[str, str]], Any] | None = None,
    ) -> None:
        self._app_id = _credential("VOLC_APP_ID")
        self._access_token = _credential("VOLC_ACCESS_TOKEN")
        self._voice = str(config.get("voice_type") or "").strip()
        if not self._voice:
            raise ProviderError("volcengine tts voice_type is required")
        default_resource = "volc.megatts.default" if self._voice.startswith("S_") else "volc.service_type.10029"
        self._resource_id = str(config.get("resource_id") or "").strip() or default_resource
        self._connect = connect or _default_connect

    def _request(self, event: int, text: str | None = None) -> bytes:
        params: dict[str, Any] = {
            "speaker": self._voice,
            "audio_params": {"format": "pcm", "sample_rate": PHONE_RATE},
        }
        if text is not None:
            params["text"] = text
        body = {"user": {"uid": "vr-ai"}, "event": event, "namespace": "BidirectionalTTS", "req_params": params}
        return json.dumps(body, ensure_ascii=False).encode("utf-8")

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        headers = {
            "X-Api-App-Key": self._app_id,
            "X-Api-Access-Key": self._access_token,
            "X-Api-Resource-Id": self._resource_id,
            "X-Api-Connect-Id": str(uuid.uuid4()),
        }
        session_id = str(uuid.uuid4())
        started = time.monotonic()
        total = 0
        clip = (text[:80] + "…") if len(text) > 80 else text
        logger.info("tts start voice=%s resource=%s text=%r", self._voice, self._resource_id, clip)
        try:
            async with self._connect(_ENDPOINT, headers) as ws:
                await ws.send(_client_frame(_START_CONNECTION, b"{}"))
                await _expect(ws, _CONNECTION_STARTED)
                await ws.send(_client_frame(_START_SESSION, self._request(_START_SESSION), session_id))
                await _expect(ws, _SESSION_STARTED)
                await ws.send(_client_frame(_TASK_REQUEST, self._request(_TASK_REQUEST, text), session_id))
                await ws.send(_client_frame(_FINISH_SESSION, b"{}", session_id))
                async for raw in ws:
                    message = _parse(raw)
                    if message.msg_type == _AUDIO_SERVER and message.payload:
                        total += len(message.payload)
                        yield message.payload
                    elif message.event == _SESSION_FINISHED:
                        break
                else:
                    raise ProviderError("volcengine tts closed before the session finished")
                await ws.send(_client_frame(_FINISH_CONNECTION, b"{}"))
        except ProviderError:
            raise
        except Exception as exc:
            logger.exception("tts failed text=%r", clip)
            raise ProviderError(f"volcengine tts failed: {exc}") from exc
        logger.info("tts done ms=%.0f pcm_bytes=%s", (time.monotonic() - started) * 1000, total)
