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
    monkeypatch.setenv("TEST_LLM_API_KEY", "sk-test")
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
        {"base_url": "https://llm.example/v1/", "api_key_env": "TEST_LLM_API_KEY", "model": "m1"},
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


def test_llm_malformed_sse_raises_provider_error():
    llm = OpenAiLlm(
        {"base_url": "https://llm.example/v1", "model": "m1"},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=b"data: not-json\n\n", headers={"content-type": "text/event-stream"}
            )
        ),
    )
    with pytest.raises(ProviderError, match="bad response"):
        _collect(llm, [])


def _llm_returning(content: bytes) -> OpenAiLlm:
    return OpenAiLlm(
        {"base_url": "https://llm.example/v1", "model": "m1"},
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content, headers={"content-type": "text/event-stream"})
        ),
    )


def test_llm_non_object_chunk_raises_provider_error():
    with pytest.raises(ProviderError, match="bad response"):
        _collect(_llm_returning(b"data: []\n\n"), [])


def test_llm_in_stream_error_raises_provider_error():
    with pytest.raises(ProviderError, match="overloaded"):
        _collect(_llm_returning(b'data: {"error": {"message": "overloaded"}}\n\n'), [])


def test_llm_empty_choices_chunk_is_ignored():
    llm = _llm_returning(_sse({"choices": []}, {"choices": [{"delta": {"content": "你好"}}]}))
    assert _collect(llm, []) == [{"text": "你好"}]


def test_llm_connect_error_raises_provider_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    llm = OpenAiLlm({"base_url": "https://llm.example/v1", "model": "m1"}, transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match="request failed"):
        _collect(llm, [])


def test_missing_key_env_is_rejected(monkeypatch):
    monkeypatch.delenv("NOT_SET_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="NOT_SET_API_KEY"):
        OpenAiLlm({"base_url": "https://llm.example/v1", "api_key_env": "NOT_SET_API_KEY"})


def test_inline_api_key_is_sent_as_bearer_token():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_sse({"choices": [{"delta": {"content": "好"}}]}))

    llm = OpenAiLlm(
        {"base_url": "https://llm.example/v1", "api_key": "sk-inline", "model": "m1"},
        transport=httpx.MockTransport(handler),
    )
    assert _collect(llm, []) == [{"text": "好"}]
    assert seen["auth"] == "Bearer sk-inline"


def test_key_env_must_be_an_api_key_name(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "jwt-secret")
    with pytest.raises(ProviderError, match="api_key_env"):
        OpenAiLlm({"base_url": "https://llm.example/v1", "api_key_env": "JWT_SECRET"})


def test_missing_base_url_is_rejected():
    with pytest.raises(ProviderError, match="base_url"):
        OpenAiLlm({"model": "m1"})
