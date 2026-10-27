"""Adapters for OpenAI-compatible HTTP APIs (DeepSeek, Qwen, SiliconFlow, Ollama, vLLM...)."""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from vr_ai.providers.base import ProviderError

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
        key_env = str(config.get("api_key_env") or "")
        key = os.environ.get(key_env, "") if key_env else ""
        if key_env and not key:
            raise ProviderError(f"environment variable {key_env} is empty")
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._timeout = float(config.get("timeout") or 10)
        self._transport = transport

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._timeout, transport=self._transport)


async def _raise_for_status(resp: httpx.Response, kind: str) -> None:
    if resp.status_code >= 400:
        detail = (await resp.aread()).decode("utf-8", errors="replace")[:300]
        raise ProviderError(f"{kind} http {resp.status_code}: {detail}")


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
                        choices = json.loads(data).get("choices") or [{}]
                        delta = choices[0].get("delta") or {}
                        if delta.get("content"):
                            yield {"text": delta["content"]}
                        for call in delta.get("tool_calls") or []:
                            name = (call.get("function") or {}).get("name")
                            if name:
                                tool_names[int(call.get("index") or 0)] = name
        except httpx.HTTPError as exc:
            raise ProviderError(f"llm request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"llm bad response: {exc}") from exc
        for name in tool_names.values():
            if name in _TOOL_ACTIONS:
                yield {"action": _TOOL_ACTIONS[name]}
