"""ai_gw: mod_audio_stream WebSocket endpoint running one BotSession per call."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os

import redis.asyncio as redis_async
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from vr_ai.providers.registry import build_asr, build_llm, build_tts
from vr_ai.scene import SceneBundle, load_scene
from vr_ai.session import BotSession, SendCommand, command_for, fallback_action
from vr_common.db import get_database_url
from vr_common.redis_keys import AI_COMMANDS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vr.ai")

app = FastAPI()
_sessions: sessionmaker[Session] | None = None
_redis: redis_async.Redis | None = None


def find_scene(scene_id: str) -> SceneBundle | None:
    global _sessions
    if _sessions is None:
        _sessions = sessionmaker(bind=create_engine(get_database_url(), pool_pre_ping=True))
    with _sessions() as db:
        return load_scene(db, scene_id)


def command_sink(call_id: str, uuid: str) -> SendCommand:
    global _redis
    if _redis is None:
        _redis = redis_async.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"), decode_responses=True)
    client = _redis

    async def send(kind: str, extra: dict) -> None:
        payload = {"type": kind, "call_id": call_id, "uuid": uuid, **extra}
        logger.info("ai command %s", payload)
        try:
            await client.lpush(AI_COMMANDS, json.dumps(payload))
        except Exception:
            logger.exception("ai command push failed %s", payload)

    return send


def stream_audio_message(wav: bytes) -> str:
    data = {"audioDataType": "wav", "sampleRate": 8000, "audioData": base64.b64encode(wav).decode("ascii")}
    return json.dumps({"type": "streamAudio", "data": data})


@app.websocket("/fs/stream")
async def fs_stream(ws: WebSocket) -> None:
    await ws.accept()
    try:
        meta = json.loads(await ws.receive_text())
    except (KeyError, ValueError):
        meta = None
    if not isinstance(meta, dict) or not meta.get("uuid"):
        logger.error("invalid stream metadata, closing: %r", meta)
        await ws.close()
        return
    call_id = str(meta.get("call_id") or "")
    uuid = str(meta.get("uuid") or "")
    scene_id = str(meta.get("scene_id") or "")
    send_command = command_sink(call_id, uuid)

    try:
        bundle = await asyncio.to_thread(find_scene, scene_id)
    except Exception:
        logger.exception("scene load failed call_id=%s scene_id=%s", call_id, scene_id or "-")
        await send_command("hangup", {})
        await ws.close()
        return
    if bundle is None:
        logger.warning("scene not found call_id=%s scene_id=%s", call_id, scene_id or "-")
        await send_command("hangup", {})
        await ws.close()
        return
    try:
        asr, tts, llm = build_asr(bundle.asr), build_tts(bundle.tts), build_llm(bundle.llm)
    except Exception:
        logger.exception("scene provider config invalid call_id=%s scene_id=%s", call_id, scene_id)
        kind, extra = command_for(bundle.scene, fallback_action(bundle.scene))
        await send_command(kind, extra)
        await ws.close()
        return

    async def send_audio(wav: bytes) -> None:
        await ws.send_text(stream_audio_message(wav))

    session = BotSession(
        scene=bundle.scene,
        asr=asr,
        tts=tts,
        llm=llm,
        send_audio=send_audio,
        send_command=send_command,
        call_id=call_id,
        uuid=uuid,
    )
    logger.info(
        "bot session start call_id=%s uuid=%s scene_id=%s asr=%s tts=%s llm=%s",
        call_id,
        uuid,
        scene_id,
        type(asr).__name__,
        type(tts).__name__,
        type(llm).__name__,
    )
    try:
        await session.start()
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                await session.on_audio(message["bytes"])
    except WebSocketDisconnect:
        pass
    finally:
        await session.close()
        logger.info("bot session end call_id=%s", call_id)
