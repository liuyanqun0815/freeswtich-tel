import asyncio
import json

import httpx
import pytest

from vr_ai.providers.base import ProviderError
from vr_ai.providers.openai_compat import OpenAiAsr, OpenAiLlm, OpenAiTts
from vr_ai.providers.registry import build_asr, build_llm, build_tts


def test_asr_posts_wav_once_per_utterance():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"text": " 你好 "})

    asr = OpenAiAsr(
        {"base_url": "https://asr.example/v1", "model": "whisper-1", "language": "zh"},
        transport=httpx.MockTransport(handler),
    )
    session = asr.open()
    session.feed(b"\x00\x00" * 800)
    session.feed(b"\x00\x00" * 800)
    assert asyncio.run(session.finish()) == "你好"
    assert seen["url"] == "https://asr.example/v1/audio/transcriptions"
    assert b"RIFF" in seen["body"]
    assert b'name="language"' in seen["body"]


def test_asr_without_audio_skips_request():
    asr = OpenAiAsr(
        {"base_url": "https://asr.example/v1"},
        transport=httpx.MockTransport(lambda request: pytest.fail("no request expected")),
    )
    assert asyncio.run(asr.open().finish()) == ""


def test_asr_http_error_raises_provider_error():
    asr = OpenAiAsr(
        {"base_url": "https://asr.example/v1"},
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom")),
    )
    session = asr.open()
    session.feed(b"\x00\x00" * 10)
    with pytest.raises(ProviderError, match="500"):
        asyncio.run(session.finish())


def test_tts_requests_pcm_and_resamples_to_8k():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"\x00\x00" * 2400)

    tts = OpenAiTts(
        {"base_url": "https://tts.example/v1", "model": "tts-1", "voice": "alloy", "sample_rate": 24000},
        transport=httpx.MockTransport(handler),
    )

    async def run() -> bytes:
        return b"".join([chunk async for chunk in tts.stream("你好")])

    out = asyncio.run(run())
    assert abs(len(out) // 2 - 800) <= 16
    assert seen["url"] == "https://tts.example/v1/audio/speech"
    assert seen["body"] == {"model": "tts-1", "voice": "alloy", "input": "你好", "response_format": "pcm"}


def test_registry_builds_openai_and_rejects_unknown_vendor():
    assert isinstance(build_asr({"vendor": "openai", "base_url": "https://x/v1"}), OpenAiAsr)
    assert isinstance(build_tts({"vendor": "openai", "base_url": "https://x/v1"}), OpenAiTts)
    assert isinstance(build_llm({"vendor": "openai", "base_url": "https://x/v1"}), OpenAiLlm)
    with pytest.raises(ProviderError, match="aliyun"):
        build_asr({"vendor": "aliyun"})
