import asyncio
import base64
import json
from collections.abc import AsyncIterator

import pytest

from vr_ai.providers.base import ProviderError
from vr_ai.providers.registry import build_tts
from vr_ai.providers.tencent_tts import TencentTts, tc3_headers

_CONFIG = {"vendor": "tencent", "voice_id": "v-female-R2s4N9qJ"}


@pytest.fixture(autouse=True)
def _tencent_env(monkeypatch):
    monkeypatch.setenv("TENCENT_SECRET_ID", "AKIDtest")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "secret-key")
    monkeypatch.setenv("TENCENT_TRTC_SDK_APP_ID", "1400000000")


def test_tc3_headers_are_stable_for_fixed_timestamp():
    headers = tc3_headers(
        secret_id="AKIDtest",
        secret_key="secret-key",
        payload=b'{"Text":"hi"}',
        region="ap-guangzhou",
        timestamp=1710000000,
    )
    assert headers["Host"] == "trtc.ai.tencentcloudapi.com"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-TC-Action"] == "TextToSpeechSSE"
    assert headers["X-TC-Version"] == "2019-07-22"
    assert headers["X-TC-Region"] == "ap-guangzhou"
    assert headers["X-TC-Timestamp"] == "1710000000"
    assert headers["Authorization"].startswith("TC3-HMAC-SHA256 Credential=AKIDtest/")
    assert "SignedHeaders=content-type;host;x-tc-action" in headers["Authorization"]
    assert "Signature=" in headers["Authorization"]


def _sse(*events: dict) -> list[str]:
    return [f"data: {json.dumps(event, ensure_ascii=False)}" for event in events] + [""]


def _tts(lines: list[str], seen: dict | None = None, **config) -> TencentTts:
    def stream_post(headers: dict[str, str], body: bytes) -> AsyncIterator[str]:
        if seen is not None:
            seen["headers"] = headers
            seen["body"] = json.loads(body)
            seen["url"] = "https://trtc.ai.tencentcloudapi.com/"

        async def gen() -> AsyncIterator[str]:
            for line in lines:
                yield line

        return gen()

    return TencentTts({**_CONFIG, **config}, stream_post=stream_post, now=lambda: 1710000000)


def _speak(tts: TencentTts, text: str = "你好") -> bytes:
    async def run() -> bytes:
        return b"".join([chunk async for chunk in tts.stream(text)])

    return asyncio.run(run())


def test_stream_decodes_pcm_chunks_and_stops_on_is_end():
    audio0 = b"\x01\x02"
    audio1 = b"\x03\x04"
    seen: dict = {}
    pcm = _speak(
        _tts(
            _sse(
                {
                    "Type": "chunk",
                    "Audio": base64.b64encode(audio0).decode(),
                    "Seq": 0,
                    "IsEnd": False,
                },
                {
                    "Type": "chunk",
                    "Audio": base64.b64encode(audio1).decode(),
                    "Seq": 1,
                    "IsEnd": True,
                },
            ),
            seen,
        )
    )
    assert pcm == audio0 + audio1
    assert seen["url"] == "https://trtc.ai.tencentcloudapi.com/"
    assert seen["body"]["Text"] == "你好"
    assert seen["body"]["Voice"] == {"VoiceId": "v-female-R2s4N9qJ"}
    assert seen["body"]["SdkAppId"] == 1400000000
    assert seen["body"]["AudioFormat"] == {"Format": "pcm", "SampleRate": 8000}
    assert seen["body"]["Model"] == "flow_02_turbo"
    assert seen["headers"]["X-TC-Action"] == "TextToSpeechSSE"


def test_stream_raises_on_sse_error_event():
    with pytest.raises(ProviderError, match="InternalError"):
        _speak(
            _tts(
                _sse(
                    {
                        "Type": "error",
                        "Error": {"Code": "InternalError", "Message": "boom"},
                    }
                )
            )
        )


def test_stream_raises_on_pre_sse_json_error():
    payload = {
        "Response": {
            "Error": {"Code": "InvalidParameter.VoiceId", "Message": "bad voice"},
            "RequestId": "req-1",
        }
    }

    def stream_post(headers: dict[str, str], body: bytes) -> AsyncIterator[str]:
        async def gen() -> AsyncIterator[str]:
            yield json.dumps(payload)

        return gen()

    tts = TencentTts(_CONFIG, stream_post=stream_post, now=lambda: 1710000000)
    with pytest.raises(ProviderError, match="InvalidParameter.VoiceId"):
        _speak(tts)


def test_voice_id_and_sdk_app_id_required(monkeypatch):
    with pytest.raises(ProviderError, match="voice_id"):
        TencentTts({"vendor": "tencent"})
    monkeypatch.delenv("TENCENT_TRTC_SDK_APP_ID", raising=False)
    with pytest.raises(ProviderError, match="TENCENT_TRTC_SDK_APP_ID"):
        TencentTts(_CONFIG)


def test_registry_builds_tencent_tts():
    assert isinstance(build_tts(_CONFIG), TencentTts)


def test_model_and_region_can_be_overridden():
    seen: dict = {}
    _speak(
        _tts(
            _sse({"Type": "chunk", "Audio": base64.b64encode(b"\x00").decode(), "IsEnd": True}),
            seen,
            model="flow_01_ex",
            region="ap-beijing",
        )
    )
    assert seen["body"]["Model"] == "flow_01_ex"
    assert seen["headers"]["X-TC-Region"] == "ap-beijing"
