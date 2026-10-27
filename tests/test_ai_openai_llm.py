import asyncio
import json

import httpx
import pytest

from vr_ai.providers.base import ProviderError
from vr_ai.providers.openai_compat import OpenAiLlm


def _sse(*chunks: dict) -> bytes:
    lines = [f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in chunks]
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode("utf-8")


def _collect(llm: OpenAiLlm, messages: list[dict]) -> list[dict]:
    async def run() -> list[dict]:
        return [item async for item in llm.stream(messages)]

    return asyncio.run(run())


def test_llm_streams_text_then_tool_action(monkeypatch):
    monkeypatch.setenv("TEST_LLM_KEY", "sk-test")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        body = _sse(
            {"choices": [{"delta": {"content": "好的，"}}]},
            {"choices": [{"delta": {"content": "马上转接。"}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "transfer_to_agent"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{}"}}]}}]},
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    llm = OpenAiLlm(
        {"base_url": "https://llm.example/v1/", "api_key_env": "TEST_LLM_KEY", "model": "m1"},
        transport=httpx.MockTransport(handler),
    )
    items = _collect(llm, [{"role": "user", "content": "转人工"}])

    assert items == [{"text": "好的，"}, {"text": "马上转接。"}, {"action": "transfer"}]
    assert seen["url"] == "https://llm.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["stream"] is True
    assert {tool["function"]["name"] for tool in seen["body"]["tools"]} == {"transfer_to_agent", "hangup"}


def test_llm_http_error_raises_provider_error():
    llm = OpenAiLlm(
        {"base_url": "https://llm.example/v1", "model": "m1"},
        transport=httpx.MockTransport(lambda request: httpx.Response(401, text="bad key")),
    )
    with pytest.raises(ProviderError, match="401"):
        _collect(llm, [])


def test_missing_key_env_is_rejected(monkeypatch):
    monkeypatch.delenv("NOT_SET_KEY", raising=False)
    with pytest.raises(ProviderError, match="NOT_SET_KEY"):
        OpenAiLlm({"base_url": "https://llm.example/v1", "api_key_env": "NOT_SET_KEY"})


def test_missing_base_url_is_rejected():
    with pytest.raises(ProviderError, match="base_url"):
        OpenAiLlm({"model": "m1"})
