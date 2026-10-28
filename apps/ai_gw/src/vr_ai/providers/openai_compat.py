"""Adapters for OpenAI-compatible HTTP APIs (DeepSeek, Qwen, SiliconFlow, Ollama, vLLM...)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from vr_ai.audio import StreamResampler, to_wav
from vr_ai.providers.base import AsrSession, ProviderError, read_secret

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "transfer_to_agent",
            "description": "用户要求人工服务，或问题超出你的处理范围时调用",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hangup",
            "description": "对话已经结束、用户道别时调用",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]
_TOOL_ACTIONS = {"transfer_to_agent": "transfer", "hangup": "hangup"}


class _Endpoint:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None) -> None:
        self.base_url = str(config.get("base_url") or "").rstrip("/")
        if not self.base_url:
            raise ProviderError("base_url is required")
        key = str(config.get("api_key") or "").strip() or read_secret(config, "api_key_env", required=False)
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._timeout = float(config.get("timeout") or 10)
        self._transport = transport

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)


async def _raise_for_status(resp: httpx.Response, kind: str) -> None:
    if resp.status_code >= 400:
        detail = (await resp.aread()).decode("utf-8", errors="replace")[:300]
        raise ProviderError(f"{kind} http {resp.status_code}: {detail}")


def _delta(data: str) -> dict[str, Any]:
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"llm bad response: {exc}") from exc
    if not isinstance(chunk, dict):
        raise ProviderError(f"llm bad response: {data[:200]}")
    if chunk.get("error"):
        raise ProviderError(f"llm stream error: {chunk['error']}")
    choices = chunk.get("choices")
    first = choices[0] if isinstance(choices, list) and choices else None
    delta = first.get("delta") if isinstance(first, dict) else None
    return delta if isinstance(delta, dict) else {}


class OpenAiLlm:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._endpoint = _Endpoint(config, transport)
        self._model = str(config.get("model") or "")
        self._temperature = config.get("temperature")

    async def stream(self, messages: list[dict]) -> AsyncIterator[dict]:
        payload: dict[str, Any] = {"model": self._model, "messages": messages, "stream": True, "tools": _TOOLS}
        if self._temperature is not None:
            payload["temperature"] = self._temperature
        tool_names: dict[int, str] = {}
        url = f"{self._endpoint.base_url}/chat/completions"
        try:
            async with self._endpoint.client() as client:
                async with client.stream("POST", url, json=payload, headers=self._endpoint.headers) as resp:
                    await _raise_for_status(resp, "llm")
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        delta = _delta(data)
                        if delta.get("content"):
                            yield {"text": delta["content"]}
                        tool_calls = delta.get("tool_calls")
                        for call in tool_calls if isinstance(tool_calls, list) else []:
                            if not isinstance(call, dict):
                                continue
                            function = call.get("function")
                            name = function.get("name") if isinstance(function, dict) else None
                            if isinstance(name, str) and name:
                                index = call.get("index")
                                tool_names[index if isinstance(index, int) else 0] = name
        except httpx.HTTPError as exc:
            raise ProviderError(f"llm request failed: {exc}") from exc
        for name in tool_names.values():
            if name in _TOOL_ACTIONS:
                yield {"action": _TOOL_ACTIONS[name]}


class _BufferedAsrSession:
    def __init__(self, asr: "OpenAiAsr") -> None:
        self._asr = asr
        self._pcm = bytearray()

    def feed(self, pcm: bytes) -> None:
        self._pcm.extend(pcm)

    async def finish(self) -> str:
        return await self._asr.transcribe(bytes(self._pcm))


class OpenAiAsr:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._endpoint = _Endpoint(config, transport)
        self._model = str(config.get("model") or "whisper-1")
        self._language = str(config.get("language") or "")

    def open(self) -> AsrSession:
        return _BufferedAsrSession(self)

    async def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        form = {"model": self._model}
        if self._language:
            form["language"] = self._language
        files = {"file": ("speech.wav", to_wav(pcm), "audio/wav")}
        url = f"{self._endpoint.base_url}/audio/transcriptions"
        try:
            async with self._endpoint.client() as client:
                resp = await client.post(url, data=form, files=files, headers=self._endpoint.headers)
        except httpx.HTTPError as exc:
            raise ProviderError(f"asr request failed: {exc}") from exc
        await _raise_for_status(resp, "asr")
        try:
            body = resp.json()
        except ValueError as exc:
            raise ProviderError(f"asr bad response: {exc}") from exc
        if not isinstance(body, dict):
            raise ProviderError(f"asr bad response: {resp.text[:200]}")
        return str(body.get("text") or "").strip()


class OpenAiTts:
    def __init__(self, config: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._endpoint = _Endpoint(config, transport)
        self._model = str(config.get("model") or "tts-1")
        self._voice = str(config.get("voice") or "alloy")
        self._sample_rate = int(config.get("sample_rate") or 24000)

    async def stream(self, text: str) -> AsyncIterator[bytes]:
        payload = {"model": self._model, "voice": self._voice, "input": text, "response_format": "pcm"}
        resampler = StreamResampler(self._sample_rate)
        url = f"{self._endpoint.base_url}/audio/speech"
        try:
            async with self._endpoint.client() as client:
                async with client.stream("POST", url, json=payload, headers=self._endpoint.headers) as resp:
                    await _raise_for_status(resp, "tts")
                    async for chunk in resp.aiter_bytes():
                        out = resampler.push(chunk)
                        if out:
                            yield out
        except httpx.HTTPError as exc:
            raise ProviderError(f"tts request failed: {exc}") from exc
        tail = resampler.flush()
        if tail:
            yield tail
