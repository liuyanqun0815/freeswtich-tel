import asyncio
import json
import struct

import pytest

from vr_ai.providers.base import ProviderError
from vr_ai.providers.registry import build_tts
from vr_ai.providers.volcengine import VolcTts

_CONFIG = {"vendor": "volcengine", "voice_type": "zh_female_shuangkuaisisi_moon_bigtts"}

_FULL_SERVER = 0b1001
_AUDIO_SERVER = 0b1011
_ERROR = 0b1111
_WITH_EVENT = 0b100


@pytest.fixture(autouse=True)
def _volc_env(monkeypatch):
    monkeypatch.setenv("VOLC_APP_ID", "app-1")
    monkeypatch.setenv("VOLC_ACCESS_TOKEN", "token-1")


def _sized(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + data


def _server(msg_type: int, event: int, payload: bytes = b"", *, session_id: str = "s", connect_id: str = "") -> bytes:
    frame = bytes([0x11, (msg_type << 4) | _WITH_EVENT, 0x10, 0x00]) + struct.pack(">i", event)
    if event in (50, 51, 52):
        frame += _sized(connect_id.encode())
    else:
        frame += _sized(session_id.encode())
    return frame + _sized(payload)


def _error(code: int, message: str) -> bytes:
    return bytes([0x11, _ERROR << 4, 0x10, 0x00]) + struct.pack(">I", code) + _sized(message.encode())


def _client_event(frame: bytes) -> tuple[int, str, dict]:
    assert frame[:4] == bytes([0x11, 0x14, 0x10, 0x00])
    event = struct.unpack(">i", frame[4:8])[0]
    rest = frame[8:]
    session_id = ""
    if event not in (1, 2):
        size = struct.unpack(">I", rest[:4])[0]
        session_id = rest[4 : 4 + size].decode()
        rest = rest[4 + size :]
    size = struct.unpack(">I", rest[:4])[0]
    return event, session_id, json.loads(rest[4 : 4 + size])


class FakeWs:
    def __init__(self, replies: list[bytes]) -> None:
        self._replies = list(replies)
        self.sent: list[bytes] = []

    async def __aenter__(self) -> "FakeWs":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def send(self, data: bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> bytes:
        if not self._replies:
            raise ConnectionError("closed")
        return self._replies.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._replies:
            raise StopAsyncIteration
        return self._replies.pop(0)


def _tts(ws: FakeWs, seen: dict | None = None, **config) -> VolcTts:
    def connect(url: str, headers: dict[str, str]) -> FakeWs:
        if seen is not None:
            seen["url"] = url
            seen["headers"] = headers
        return ws

    return VolcTts({**_CONFIG, **config}, connect=connect)


def _speak(tts: VolcTts, text: str = "你好") -> bytes:
    async def run() -> bytes:
        return b"".join([chunk async for chunk in tts.stream(text)])

    return asyncio.run(run())


def _happy_replies(*audio: bytes) -> list[bytes]:
    return [
        _server(_FULL_SERVER, 50, b"{}", connect_id="c1"),
        _server(_FULL_SERVER, 150, b"{}"),
        _server(_FULL_SERVER, 350, b'{"text":"\\u4f60\\u597d"}'),
        *[_server(_AUDIO_SERVER, 352, chunk) for chunk in audio],
        _server(_FULL_SERVER, 351, b"{}"),
        _server(_FULL_SERVER, 152, b"{}"),
    ]


def test_speaks_one_sentence_per_session_and_returns_8k_pcm():
    ws = FakeWs(_happy_replies(b"\x01\x00" * 80, b"\x02\x00" * 80))
    seen: dict = {}
    audio = _speak(_tts(ws, seen), "你好")
    assert audio == b"\x01\x00" * 80 + b"\x02\x00" * 80

    assert seen["url"] == "wss://openspeech.bytedance.com/api/v3/tts/bidirection"
    assert seen["headers"]["X-Api-App-Key"] == "app-1"
    assert seen["headers"]["X-Api-Access-Key"] == "token-1"
    assert seen["headers"]["X-Api-Resource-Id"] == "volc.service_type.10029"
    assert seen["headers"]["X-Api-Connect-Id"]

    events = [_client_event(frame) for frame in ws.sent]
    assert [event for event, _, _ in events] == [1, 100, 200, 102, 2]
    session_ids = {sid for event, sid, _ in events if event in (100, 200, 102)}
    assert len(session_ids) == 1 and "" not in session_ids
    start = events[1][2]
    assert start["namespace"] == "BidirectionalTTS"
    assert start["req_params"]["speaker"] == _CONFIG["voice_type"]
    assert start["req_params"]["audio_params"] == {"format": "pcm", "sample_rate": 8000}
    assert events[2][2]["req_params"]["text"] == "你好"


def test_resource_id_follows_voice_and_can_be_overridden():
    seen: dict = {}
    _speak(_tts(FakeWs(_happy_replies(b"\x00\x00")), seen, voice_type="S_my_voice"))
    assert seen["headers"]["X-Api-Resource-Id"] == "volc.megatts.default"

    _speak(_tts(FakeWs(_happy_replies(b"\x00\x00")), seen, resource_id="seed-tts-2.0"))
    assert seen["headers"]["X-Api-Resource-Id"] == "seed-tts-2.0"


def test_error_frame_raises_provider_error():
    ws = FakeWs([_error(45000001, '{"error":"invalid speaker"}')])
    with pytest.raises(ProviderError, match="45000001"):
        _speak(_tts(ws))


def test_session_failed_raises_provider_error():
    ws = FakeWs(
        [
            _server(_FULL_SERVER, 50, b"{}", connect_id="c1"),
            _server(_FULL_SERVER, 153, b'{"error":"quota exceeded"}'),
        ]
    )
    with pytest.raises(ProviderError, match="quota exceeded"):
        _speak(_tts(ws))


def test_connection_closed_before_session_finished_raises_provider_error():
    ws = FakeWs(_happy_replies(b"\x00\x00")[:-2])
    with pytest.raises(ProviderError, match="finished"):
        _speak(_tts(ws))


def test_connect_failure_raises_provider_error():
    def connect(url: str, headers: dict[str, str]):
        raise OSError("network unreachable")

    with pytest.raises(ProviderError, match="network unreachable"):
        _speak(VolcTts(_CONFIG, connect=connect))


@pytest.mark.parametrize("name", ["VOLC_APP_ID", "VOLC_ACCESS_TOKEN"])
def test_missing_credential_env_is_rejected(monkeypatch, name):
    monkeypatch.delenv(name)
    with pytest.raises(ProviderError, match=name):
        VolcTts(_CONFIG)


def test_voice_type_is_required():
    with pytest.raises(ProviderError, match="voice_type"):
        VolcTts({"vendor": "volcengine"})


def test_registry_builds_volc_tts():
    assert isinstance(build_tts(_CONFIG), VolcTts)
