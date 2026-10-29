"""One robot conversation: listen, think, speak, then hand control back to engine."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from vr_ai.audio import Endpointer, duration_seconds, to_wav
from vr_ai.providers.base import Asr, AsrSession, Llm, ProviderError, Tts
from vr_ai.scriptbot import opening_text

logger = logging.getLogger("vr.ai.session")

_SENTENCE_END = "。！？；!?;\n"
_HUMAN_AGENT_RE = re.compile(r"转人工|转接人工|找人工|人工客服|转客服|转接客服")
_TRANSFER_HINT = "用户明确要求人工服务时必须调用 transfer_to_agent，不要只口头答应。"
_TRANSFER_PROMPT = "正在为您转接人工。"

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


def wants_human_agent(text: str) -> bool:
    """True when the user utterance clearly asks for a human agent."""
    return bool(_HUMAN_AGENT_RE.search((text or "").strip()))


def system_messages(system_prompt: str) -> list[dict[str, str]]:
    """Build the system turn, always reminding the model to call transfer_to_agent."""
    prompt = (system_prompt or "").strip()
    if not prompt:
        return [{"role": "system", "content": _TRANSFER_HINT}]
    if "transfer_to_agent" in prompt or "转人工" in prompt:
        return [{"role": "system", "content": prompt}]
    return [{"role": "system", "content": f"{prompt}\n{_TRANSFER_HINT}"}]


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


def _clip(text: str, limit: int = 160) -> str:
    value = (text or "").replace("\n", " ").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


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
        call_id: str = "",
        uuid: str = "",
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
        self._call_id = call_id
        self._uuid = uuid
        self._history: list[dict[str, str]] = list(system_messages(scene.system_prompt))
        self._asr_session: AsrSession | None = None
        self._reply: asyncio.Task[None] | None = None
        self._speaking_until = 0.0
        self._turn = 0
        self.finished = False

    def _ctx(self) -> str:
        return (
            f"call_id={self._call_id or '-'} uuid={self._uuid or '-'} "
            f"scene={self._scene.scene_id} turn={self._turn}"
        )

    async def start(self) -> None:
        opening = self._scene.opening.strip()
        logger.info(
            "flow start %s fallback=%s opening=%s",
            self._ctx(),
            self._scene.fallback,
            "yes" if opening else "no",
        )
        if not opening:
            return
        if "智能助理" not in opening:
            opening = opening_text(opening)
        self._history.append({"role": "assistant", "content": opening})
        logger.info("flow opening %s text=%r", self._ctx(), _clip(opening))
        try:
            await self._speak(opening, stage="opening")
        except ProviderError as exc:
            logger.error("opening failed %s: %s", self._ctx(), exc)
            await self._finish(fallback_action(self._scene))
        except Exception:
            logger.exception("opening crashed %s", self._ctx())
            await self._finish(fallback_action(self._scene))

    async def on_audio(self, pcm: bytes) -> None:
        if self.finished:
            return
        for kind, frame in self._endpointer.push(pcm):
            if kind == "start":
                self._turn += 1
                logger.info("flow vad_start %s", self._ctx())
                await self._interrupt()
                self._asr_session = self._asr.open()
                logger.info("flow asr_open %s", self._ctx())
            elif kind == "audio" and self._asr_session is not None:
                self._asr_session.feed(frame)
            elif kind == "end" and self._asr_session is not None:
                session, self._asr_session = self._asr_session, None
                logger.info("flow vad_end %s → asr_finish", self._ctx())
                self._reply = asyncio.create_task(self._answer(session))

    async def drain(self) -> None:
        if self._reply is None:
            return
        for result in await asyncio.gather(self._reply, return_exceptions=True):
            if isinstance(result, Exception):
                logger.warning("reply failed %s: %r", self._ctx(), result)

    async def close(self) -> None:
        logger.info("flow close %s finished=%s", self._ctx(), self.finished)
        if self._reply is not None and not self._reply.done():
            self._reply.cancel()
        await self.drain()

    async def _interrupt(self) -> None:
        if self._reply is not None and not self._reply.done():
            logger.info("flow interrupt cancel_reply %s", self._ctx())
            self._reply.cancel()
        if self._clock() < self._speaking_until:
            self._speaking_until = 0.0
            logger.info("flow interrupt break_playback %s", self._ctx())
            await self._send_command("break", {})

    async def _answer(self, session: AsrSession) -> None:
        started = self._clock()
        try:
            text = await session.finish()
            asr_ms = int((self._clock() - started) * 1000)
            if not text:
                logger.info("flow asr_empty %s asr_ms=%s", self._ctx(), asr_ms)
                return
            logger.info("flow asr_ok %s asr_ms=%s text=%r", self._ctx(), asr_ms, _clip(text))
            self._history.append({"role": "user", "content": text})
            if wants_human_agent(text):
                logger.info("flow transfer_keyword %s text=%r", self._ctx(), _clip(text))
                try:
                    await self._speak(_TRANSFER_PROMPT, stage="transfer_prompt")
                except ProviderError as exc:
                    logger.error("transfer prompt failed %s: %s", self._ctx(), exc)
                await self._finish("transfer")
                return
            action = await self._reply_to(list(self._history))
            if action:
                await self._finish(action)
        except ProviderError as exc:
            logger.error("provider failed %s: %s", self._ctx(), exc)
            await self._finish(fallback_action(self._scene))
        except Exception:
            logger.exception("reply crashed %s", self._ctx())
            await self._finish(fallback_action(self._scene))

    async def _reply_to(self, messages: list[dict[str, str]]) -> str | None:
        spoken: list[str] = []
        action: str | None = None
        pending = ""
        started = self._clock()
        logger.info(
            "flow llm_start %s messages=%s last_user=%r",
            self._ctx(),
            len(messages),
            _clip(next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")),
        )
        try:
            async for item in self._llm.stream(messages):
                if "action" in item:
                    action = str(item["action"])
                    logger.info("flow llm_action %s action=%s", self._ctx(), action)
                    continue
                delta = str(item.get("text") or "")
                if delta:
                    logger.debug("flow llm_delta %s text=%r", self._ctx(), _clip(delta, 80))
                sentences, pending = split_sentences(pending + delta)
                for sentence in sentences:
                    logger.info("flow llm_sentence %s text=%r", self._ctx(), _clip(sentence))
                    await self._speak(sentence, stage="llm")
                    spoken.append(sentence)
            if pending.strip():
                logger.info("flow llm_tail %s text=%r", self._ctx(), _clip(pending.strip()))
                await self._speak(pending.strip(), stage="llm")
                spoken.append(pending.strip())
        finally:
            if spoken:
                self._history.append({"role": "assistant", "content": "".join(spoken)})
        llm_ms = int((self._clock() - started) * 1000)
        logger.info(
            "flow llm_done %s llm_ms=%s spoken=%s action=%s reply=%r",
            self._ctx(),
            llm_ms,
            len(spoken),
            action or "-",
            _clip("".join(spoken)),
        )
        if not spoken and not action:
            raise ProviderError("llm empty reply")
        return action

    async def _speak(self, text: str, *, stage: str = "tts") -> None:
        started = self._clock()
        logger.info("flow tts_start %s stage=%s text=%r", self._ctx(), stage, _clip(text))
        pcm = b"".join([chunk async for chunk in self._tts.stream(text)])
        tts_ms = int((self._clock() - started) * 1000)
        if not pcm:
            logger.warning("flow tts_empty %s stage=%s tts_ms=%s", self._ctx(), stage, tts_ms)
            return
        play_sec = duration_seconds(pcm)
        await self._send_audio(to_wav(pcm))
        self._speaking_until = max(self._clock(), self._speaking_until) + play_sec
        logger.info(
            "flow tts_ok %s stage=%s tts_ms=%s pcm_bytes=%s play_sec=%.2f",
            self._ctx(),
            stage,
            tts_ms,
            len(pcm),
            play_sec,
        )

    async def _finish(self, action: str) -> None:
        self.finished = True
        wait = self._speaking_until - self._clock()
        logger.info(
            "flow finish %s action=%s wait_playback_sec=%.2f",
            self._ctx(),
            action,
            max(wait, 0.0),
        )
        if wait > 0:
            await self._sleep(wait)
        kind, extra = command_for(self._scene, action)
        logger.info("flow command %s kind=%s extra=%s", self._ctx(), kind, extra)
        await self._send_command(kind, extra)
