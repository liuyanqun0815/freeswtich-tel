"""Tencent Cloud TRTC streaming TTS via TextToSpeechSSE (pcm @ 8 kHz)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from vr_ai.audio import PHONE_RATE
from vr_ai.providers.base import ProviderError

_HOST = "trtc.ai.tencentcloudapi.com"
_ENDPOINT = f"https://{_HOST}/"
_SERVICE = "trtc"
_ACTION = "TextToSpeechSSE"
_VERSION = "2019-07-22"
_DEFAULT_REGION = "ap-guangzhou"
_DEFAULT_MODEL = "flow_02_turbo"

StreamPost = Callable[[dict[str, str], bytes], AsyncIterator[str]]


def _credential(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ProviderError(f"environment variable {name} is empty")
    return value


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def tc3_headers(
    *,
    secret_id: str,
    secret_key: str,
    payload: bytes,
    region: str,
    timestamp: int,
) -> dict[str, str]:
    """Build TC3 signed headers for TextToSpeechSSE on trtc.ai.tencentcloudapi.com."""
    date = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d")
    canonical_headers = (
        f"content-type:application/json\nhost:{_HOST}\nx-tc-action:{_ACTION.lower()}\n"
    )
    signed_headers = "content-type;host;x-tc-action"
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "",
            canonical_headers,
            signed_headers,
            _sha256_hex(payload),
        ]
    )
    credential_scope = f"{date}/{_SERVICE}/tc3_request"
    string_to_sign = "\n".join(
        [
            "TC3-HMAC-SHA256",
            str(timestamp),
            credential_scope,
            _sha256_hex(canonical_request.encode("utf-8")),
        ]
    )
    secret_date = _hmac_sha256(("TC3" + secret_key).encode("utf-8"), date)
    secret_service = _hmac_sha256(secret_date, _SERVICE)
    secret_signing = _hmac_sha256(secret_service, "tc3_request")
    signature = hmac.new(secret_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "TC3-HMAC-SHA256 "
        f"Credential={secret_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )
    return {
        "Authorization": authorization,
        "Content-Type": "application/json",
        "Host": _HOST,
        "X-TC-Action": _ACTION,
        "X-TC-Timestamp": str(timestamp),
        "X-TC-Version": _VERSION,
        "X-TC-Region": region,
    }


def _raise_api_error(payload: dict[str, Any]) -> None:
    error = payload.get("Error")
    if isinstance(error, dict):
        code = error.get("Code") or "Error"
        message = error.get("Message") or "-"
        raise ProviderError(f"tencent tts {code}: {message}")
    response = payload.get("Response")
    if isinstance(response, dict):
        _raise_api_error(response)
        raise ProviderError(f"tencent tts unexpected response: {str(payload)[:200]}")
    raise ProviderError(f"tencent tts unexpected payload: {str(payload)[:200]}")


def _default_stream_post(headers: dict[str, str], body: bytes) -> AsyncIterator[str]:
    async def gen() -> AsyncIterator[str]:
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", _ENDPOINT, headers=headers, content=body) as response:
                content_type = (response.headers.get("content-type") or "").lower()
                if "application/json" in content_type and "text/event-stream" not in content_type:
                    raw = await response.aread()
                    try:
                        payload = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ProviderError(
                            f"tencent tts bad json error body: {raw[:200]!r}"
                        ) from exc
                    if not isinstance(payload, dict):
                        raise ProviderError(f"tencent tts bad error body: {raw[:200]!r}")
                    _raise_api_error(payload)
                if response.status_code >= 400:
                    raw = await response.aread()
                    raise ProviderError(f"tencent tts http {response.status_code}: {raw[:200]!r}")
                async for line in response.aiter_lines():
                    yield line

    return gen()


class TencentTts:
    """Credentials from .env; scene picks voice_id / optional model / region."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        stream_post: StreamPost | None = None,
        now: Callable[[], int] | None = None,
    ) -> None:
        self._secret_id = _credential("TENCENT_SECRET_ID")
        self._secret_key = _credential("TENCENT_SECRET_KEY")
        sdk_app_id = _credential("TENCENT_TRTC_SDK_APP_ID")
        if not sdk_app_id.isdigit():
            raise ProviderError("environment variable TENCENT_TRTC_SDK_APP_ID must be numeric")
        self._sdk_app_id = int(sdk_app_id)
        self._voice_id = str(config.get("voice_id") or "").strip()
        if not self._voice_id:
            raise ProviderError("tencent tts voice_id is required")
        self._model = str(config.get("model") or "").strip() or _DEFAULT_MODEL
        self._region = str(config.get("region") or "").strip() or _DEFAULT_REGION
        self._stream_post = stream_post or _default_stream_post
        self._now = now or (lambda: int(time.time()))

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        payload = {
            "Text": text,
            "SdkAppId": self._sdk_app_id,
            "Voice": {"VoiceId": self._voice_id},
            "AudioFormat": {"Format": "pcm", "SampleRate": PHONE_RATE},
            "Model": self._model,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = tc3_headers(
            secret_id=self._secret_id,
            secret_key=self._secret_key,
            payload=body,
            region=self._region,
            timestamp=self._now(),
        )
        try:
            async for line in self._stream_post(headers, body):
                chunk = _parse_sse_line(line)
                if chunk is None:
                    continue
                data, is_end = chunk
                if data:
                    yield data
                if is_end:
                    return
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"tencent tts failed: {exc}") from exc


def _parse_sse_line(line: str) -> tuple[bytes, bool] | None:
    text = line.strip()
    if not text:
        return None
    if text.startswith("data:"):
        raw = text[5:].strip()
        if not raw or raw == "[DONE]":
            return None
        try:
            event = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"tencent tts bad sse data: {raw[:200]}") from exc
        if not isinstance(event, dict):
            raise ProviderError(f"tencent tts bad sse event: {raw[:200]}")
        event_type = str(event.get("Type") or "").lower()
        if event_type == "error":
            _raise_api_error(event)
        if event_type != "chunk":
            return None
        audio_b64 = event.get("Audio") or ""
        pcm = base64.b64decode(audio_b64) if audio_b64 else b""
        return pcm, bool(event.get("IsEnd"))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        _raise_api_error(payload)
    return None
