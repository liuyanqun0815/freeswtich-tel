"""One robot conversation: listen, think, speak, then hand control back to engine."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from vr_ai.audio import Endpointer, duration_seconds, to_wav
from vr_ai.providers.base import Asr, AsrSession, Llm, ProviderError, Tts
from vr_ai.scriptbot import opening_text

logger = logging.getLogger("vr.ai.session")

_SENTENCE_END = "。！？；!?;\n"

SendAudio = Callable[[bytes], Awaitable[None]]
SendCommand = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class SceneConfig:
    scene_id: str
    opening: str = ""
    system_prompt: str = ""
    fallback: str = "transfer"
    transfer_skill_group_id: int | None = None


def fallback_action(scene: SceneConfig) -> str:
    return "transfer" if scene.fallback == "transfer" else "hangup"


def command_for(scene: SceneConfig, action: str) -> tuple[str, dict[str, Any]]:
    if action == "transfer":
        return "transfer", {"skill_group_id": scene.transfer_skill_group_id}
    return "hangup", {}


def split_sentences(text: str) -> tuple[list[str], str]:
    sentences: list[str] = []
    start = 0
    for index, char in enumerate(text):
        if char in _SENTENCE_END:
            sentence = text[start : index + 1].strip()
            if sentence:
                sentences.append(sentence)
            start = index + 1
    return sentences, text[start:]


class BotSession:
    def __init__(
        self,
        *,
        scene: SceneConfig,
        asr: Asr,
        tts: Tts,
        llm: Llm,
        send_audio: SendAudio,
        send_command: SendCommand,
        endpointer: Endpointer | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._scene = scene
        self._asr = asr
        self._tts = tts
        self._llm = llm
        self._send_audio = send_audio
        self._send_command = send_command
        self._endpointer = endpointer or Endpointer()
        self._clock = clock
        self._sleep = sleep
        self._history: list[dict[str, str]] = []
        if scene.system_prompt:
            self._history.append({"role": "system", "content": scene.system_prompt})
        self._asr_session: AsrSession | None = None
        self._reply: asyncio.Task[None] | None = None
        self._speaking_until = 0.0
        self.finished = False

    async def start(self) -> None:
        opening = self._scene.opening.strip()
        if not opening:
            return
        if "智能助理" not in opening:
            opening = opening_text(opening)
        self._history.append({"role": "assistant", "content": opening})
        try:
            await self._speak(opening)
        except ProviderError as exc:
            logger.error("opening failed scene=%s: %s", self._scene.scene_id, exc)
            await self._finish(fallback_action(self._scene))
        except Exception:
            logger.exception("opening crashed scene=%s", self._scene.scene_id)
            await self._finish(fallback_action(self._scene))

    async def on_audio(self, pcm: bytes) -> None:
        if self.finished:
            return
        for kind, frame in self._endpointer.push(pcm):
            if kind == "start":
                await self._interrupt()
                self._asr_session = self._asr.open()
            elif kind == "audio" and self._asr_session is not None:
                self._asr_session.feed(frame)
            elif kind == "end" and self._asr_session is not None:
                session, self._asr_session = self._asr_session, None
                self._reply = asyncio.create_task(self._answer(session))

    async def drain(self) -> None:
        if self._reply is None:
            return
        for result in await asyncio.gather(self._reply, return_exceptions=True):
            if isinstance(result, Exception):
                logger.warning("reply failed scene=%s: %r", self._scene.scene_id, result)

    async def close(self) -> None:
        if self._reply is not None and not self._reply.done():
            self._reply.cancel()
        await self.drain()

    async def _interrupt(self) -> None:
        if self._reply is not None and not self._reply.done():
            self._reply.cancel()
        if self._clock() < self._speaking_until:
            self._speaking_until = 0.0
            await self._send_command("break", {})

    async def _answer(self, session: AsrSession) -> None:
        try:
            text = await session.finish()
            if not text:
                return
            self._history.append({"role": "user", "content": text})
            action = await self._reply_to(list(self._history))
            if action:
                await self._finish(action)
        except ProviderError as exc:
            logger.error("provider failed scene=%s: %s", self._scene.scene_id, exc)
            await self._finish(fallback_action(self._scene))
        except Exception:
            logger.exception("reply crashed scene=%s", self._scene.scene_id)
            await self._finish(fallback_action(self._scene))

    async def _reply_to(self, messages: list[dict[str, str]]) -> str | None:
        spoken: list[str] = []
        action: str | None = None
        pending = ""
        try:
            async for item in self._llm.stream(messages):
                if "action" in item:
                    action = str(item["action"])
                    continue
                sentences, pending = split_sentences(pending + str(item.get("text") or ""))
                for sentence in sentences:
                    await self._speak(sentence)
                    spoken.append(sentence)
            if pending.strip():
                await self._speak(pending.strip())
                spoken.append(pending.strip())
        finally:
            if spoken:
                self._history.append({"role": "assistant", "content": "".join(spoken)})
        return action

    async def _speak(self, text: str) -> None:
        pcm = b"".join([chunk async for chunk in self._tts.stream(text)])
        if not pcm:
            return
        await self._send_audio(to_wav(pcm))
        self._speaking_until = max(self._clock(), self._speaking_until) + duration_seconds(pcm)

    async def _finish(self, action: str) -> None:
        wait = self._speaking_until - self._clock()
        if wait > 0:
            await self._sleep(wait)
        kind, extra = command_for(self._scene, action)
        self.finished = True
        await self._send_command(kind, extra)
