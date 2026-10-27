import base64
import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import vr_ai.server as server
from vr_ai.providers.base import ProviderError
from vr_ai.scene import SceneBundle
from vr_ai.session import SceneConfig


class FakeTts:
    async def stream(self, text: str):
        yield b"\x00\x00" * 800


def _install(monkeypatch, bundle: SceneBundle | None, *, llm_error: bool = False) -> list[tuple]:
    commands: list[tuple] = []

    def build_llm(config):
        if llm_error:
            raise ProviderError("bad llm config")
        return object()

    def sink(call_id: str, uuid: str):
        async def send(kind: str, extra: dict) -> None:
            commands.append((call_id, uuid, kind, extra))

        return send

    monkeypatch.setattr(server, "find_scene", lambda scene_id: bundle)
    monkeypatch.setattr(server, "build_asr", lambda config: object())
    monkeypatch.setattr(server, "build_tts", lambda config: FakeTts())
    monkeypatch.setattr(server, "build_llm", build_llm)
    monkeypatch.setattr(server, "command_sink", sink)
    return commands


def _bundle(**scene) -> SceneBundle:
    return SceneBundle(scene=SceneConfig(scene_id="3", **scene), asr={}, tts={}, llm={})


def test_stream_speaks_opening_back_as_stream_audio(monkeypatch):
    commands = _install(monkeypatch, _bundle(opening="我是智能助理，您好"))
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "3"}))
        message = json.loads(ws.receive_text())
    assert message["type"] == "streamAudio"
    assert message["data"]["audioDataType"] == "wav"
    assert message["data"]["sampleRate"] == 8000
    assert base64.b64decode(message["data"]["audioData"])[:4] == b"RIFF"
    assert commands == []


def test_unknown_scene_hangs_up(monkeypatch):
    commands = _install(monkeypatch, None)
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "404"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert commands == [("c1", "u1", "hangup", {})]


def test_bad_provider_config_uses_scene_fallback(monkeypatch):
    commands = _install(monkeypatch, _bundle(fallback="transfer", transfer_skill_group_id=7), llm_error=True)
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "3"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert commands == [("c1", "u1", "transfer", {"skill_group_id": 7})]


def test_bad_metadata_closes_without_command(monkeypatch):
    commands = _install(monkeypatch, _bundle(opening="您好"))
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text("not json")
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert commands == []


def test_scene_load_error_hangs_up(monkeypatch):
    commands = _install(monkeypatch, None)

    def find_scene(scene_id: str):
        raise RuntimeError("db down")

    monkeypatch.setattr(server, "find_scene", find_scene)
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "3"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert commands == [("c1", "u1", "hangup", {})]


def test_unexpected_provider_error_uses_scene_fallback(monkeypatch):
    commands = _install(monkeypatch, _bundle(fallback="hangup"))

    def build_llm(config):
        raise ValueError("bad timeout")

    monkeypatch.setattr(server, "build_llm", build_llm)
    with TestClient(server.app).websocket_connect("/fs/stream") as ws:
        ws.send_text(json.dumps({"call_id": "c1", "uuid": "u1", "scene_id": "3"}))
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert commands == [("c1", "u1", "hangup", {})]
