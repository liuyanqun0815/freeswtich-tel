import asyncio
import base64
import hashlib
import hmac
import json
from urllib.parse import quote

import pytest

from vr_ai.providers.base import ProviderError
from vr_ai.providers.registry import build_asr
from vr_ai.providers.tencent import TencentAsr, signed_url

_CONFIG = {"vendor": "tencent"}


@pytest.fixture(autouse=True)
def _tencent_env(monkeypatch):
    monkeypatch.setenv("TENCENT_APP_ID", "1250000000")
    monkeypatch.setenv("TENCENT_SECRET_ID", "AKIDtest")
    monkeypatch.setenv("TENCENT_SECRET_KEY", "secret-key")


class FakeWs:
    """Replies with a handshake at once, then the scripted results after the client sends end."""

    def __init__(self, replies: list[dict], *, close_early: bool = False, hang: bool = False) -> None:
        self.replies = replies
        self.close_early = close_early
        self.hang = hang
        self.sent: list = []
        self._ended = asyncio.Event()

    async def __aenter__(self) -> "FakeWs":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def send(self, data) -> None:
        self.sent.append(data)
        if isinstance(data, str) and json.loads(data).get("type") == "end":
            self._ended.set()

    def __aiter__(self):
        return self._messages()

    async def _messages(self):
        yield json.dumps({"code": 0, "message": "success", "voice_id": "v1"})
        if self.close_early:
            return
        await self._ended.wait()
        if self.hang:
            await asyncio.Event().wait()
        for reply in self.replies:
            yield json.dumps(reply, ensure_ascii=False)


def _asr(ws: FakeWs, urls: list[str] | None = None, **config) -> TencentAsr:
    def connect(url: str) -> FakeWs:
        if urls is not None:
            urls.append(url)
        return ws

    return TencentAsr({**_CONFIG, **config}, connect=connect)


def _recognize(asr: TencentAsr, *chunks: bytes) -> str:
    async def run() -> str:
        session = asr.open()
        for chunk in chunks:
            session.feed(chunk)
        return await session.finish()

    return asyncio.run(run())


def test_signed_url_sorts_params_and_signs_with_hmac_sha1():
    url = signed_url(
        "1250000000",
        "AKIDtest",
        "secret-key",
        {"engine_model_type": "8k_zh", "voice_format": 1, "needvad": 0},
        timestamp=1700000000,
        nonce=42,
        voice_id="abc",
    )
    plain = (
        "asr.cloud.tencent.com/asr/v2/1250000000?engine_model_type=8k_zh&expired=1700086400&needvad=0"
        "&nonce=42&secretid=AKIDtest&timestamp=1700000000&voice_format=1&voice_id=abc"
    )
    digest = hmac.new(b"secret-key", plain.encode("utf-8"), hashlib.sha1).digest()
    assert url == f"wss://{plain}&signature={quote(base64.b64encode(digest).decode('ascii'))}"


def test_streams_audio_then_end_and_joins_results_by_index():
    ws = FakeWs(
        [
            {"code": 0, "result": {"slice_type": 1, "index": 0, "voice_text_str": "你"}, "final": 0},
            {"code": 0, "result": {"slice_type": 2, "index": 1, "voice_text_str": "请问"}, "final": 0},
            {"code": 0, "result": {"slice_type": 2, "index": 0, "voice_text_str": "你好，"}, "final": 0},
            {"code": 0, "final": 1},
        ]
    )
    urls: list[str] = []
    text = _recognize(_asr(ws, urls), b"\x01\x00" * 160, b"\x02\x00" * 160)
    assert text == "你好，请问"
    assert ws.sent == [b"\x01\x00" * 160, b"\x02\x00" * 160, json.dumps({"type": "end"})]
    assert urls[0].startswith("wss://asr.cloud.tencent.com/asr/v2/1250000000?engine_model_type=8k_zh&")
    assert "secretid=AKIDtest" in urls[0]
    assert "&signature=" in urls[0]


def test_engine_model_type_is_configurable():
    urls: list[str] = []
    _recognize(_asr(FakeWs([{"code": 0, "final": 1}]), urls, engine_model_type="16k_zh"))
    assert "engine_model_type=16k_zh" in urls[0]


def test_error_code_raises_provider_error():
    ws = FakeWs([{"code": 4002, "message": "鉴权失败"}])
    with pytest.raises(ProviderError, match="4002"):
        _recognize(_asr(ws), b"\x00\x00" * 160)


def test_connection_closed_before_final_raises_provider_error():
    with pytest.raises(ProviderError, match="final"):
        _recognize(_asr(FakeWs([], close_early=True)), b"\x00\x00" * 160)


def test_no_final_result_times_out():
    with pytest.raises(ProviderError, match="timed out"):
        _recognize(_asr(FakeWs([], hang=True), timeout=0.05), b"\x00\x00" * 160)


def test_connect_failure_raises_provider_error():
    def connect(url: str):
        raise OSError("network unreachable")

    with pytest.raises(ProviderError, match="network unreachable"):
        _recognize(TencentAsr(_CONFIG, connect=connect))


@pytest.mark.parametrize("name", ["TENCENT_APP_ID", "TENCENT_SECRET_ID", "TENCENT_SECRET_KEY"])
def test_missing_credential_env_is_rejected(monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(ProviderError, match=name):
        TencentAsr(_CONFIG)


def test_non_numeric_app_id_is_rejected(monkeypatch):
    monkeypatch.setenv("TENCENT_APP_ID", "my-app")
    with pytest.raises(ProviderError, match="TENCENT_APP_ID"):
        TencentAsr(_CONFIG)


def test_registry_builds_tencent_asr():
    assert isinstance(build_asr(_CONFIG), TencentAsr)
