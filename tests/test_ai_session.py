import asyncio
from collections.abc import Awaitable, Callable

from vr_ai.audio import Endpointer
from vr_ai.providers.base import ProviderError
from vr_ai.session import BotSession, SceneConfig, command_for, split_sentences

VOICED = b"\x01\x00" * 160
SILENT = bytes(320)
ONE_SECOND = b"\x00\x00" * 8000


class FakeAsrSession:
    def __init__(self, text: str, error: Exception | None) -> None:
        self._text = text
        self._error = error

    def feed(self, pcm: bytes) -> None:
        return None

    async def finish(self) -> str:
        if self._error is not None:
            raise self._error
        return self._text


class FakeAsr:
    def __init__(self, *texts: str, error: Exception | None = None) -> None:
        self._texts = list(texts)
        self._error = error

    def open(self) -> FakeAsrSession:
        return FakeAsrSession(self._texts.pop(0) if self._texts else "", self._error)


class FakeTts:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def stream(self, text: str):
        self.texts.append(text)
        yield ONE_SECOND


class CrashingTts(FakeTts):
    async def stream(self, text: str):
        self.texts.append(text)
        raise RuntimeError("boom")
        yield ONE_SECOND


class FakeLlm:
    def __init__(self, *items: dict) -> None:
        self._items = list(items)
        self.calls: list[list[dict]] = []

    async def stream(self, messages: list[dict]):
        self.calls.append(messages)
        for item in self._items:
            yield item


class CrashingLlm(FakeLlm):
    async def stream(self, messages: list[dict]):
        self.calls.append(messages)
        raise RuntimeError("boom")
        yield {}


class Harness:
    def __init__(
        self,
        scene: SceneConfig,
        asr: FakeAsr | None = None,
        llm: FakeLlm | None = None,
        tts: FakeTts | None = None,
    ) -> None:
        self.audio: list[bytes] = []
        self.commands: list[tuple[str, dict]] = []
        self.sleeps: list[float] = []
        self.tts = tts or FakeTts()
        self.llm = llm or FakeLlm()
        self.on_sleep: Callable[[], Awaitable[None]] | None = None

        async def send_audio(wav: bytes) -> None:
            self.audio.append(wav)

        async def send_command(kind: str, extra: dict) -> None:
            self.commands.append((kind, extra))

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)
            if self.on_sleep is not None:
                await self.on_sleep()

        self.session = BotSession(
            scene=scene,
            asr=asr or FakeAsr(),
            tts=self.tts,
            llm=self.llm,
            send_audio=send_audio,
            send_command=send_command,
            endpointer=Endpointer(is_speech=lambda frame, rate: frame[0] != 0),
            clock=lambda: 0.0,
            sleep=sleep,
        )

    async def say(self) -> None:
        await self.session.on_audio(VOICED * 10 + SILENT * 30)
        await self.session.drain()


def _run(coro_factory):
    return asyncio.run(coro_factory())


def test_split_sentences_keeps_unfinished_tail():
    assert split_sentences("好的。请稍等，") == (["好的。"], "请稍等，")


def test_command_for_transfer_carries_skill_group():
    scene = SceneConfig(scene_id="1", transfer_skill_group_id=7)
    assert command_for(scene, "transfer") == ("transfer", {"skill_group_id": 7})
    assert command_for(scene, "hangup") == ("hangup", {})


def test_opening_gets_assistant_prefix_and_is_sent_as_wav():
    async def run():
        harness = Harness(SceneConfig(scene_id="1", opening="您好"))
        await harness.session.start()
        return harness

    harness = _run(run)
    assert harness.tts.texts == ["我是智能助理。您好"]
    assert harness.audio[0][:4] == b"RIFF"


def test_utterance_is_answered_sentence_by_sentence():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", system_prompt="你是客服"),
            asr=FakeAsr("查订单"),
            llm=FakeLlm({"text": "好的。请"}, {"text": "稍等"}),
        )
        await harness.say()
        return harness

    harness = _run(run)
    assert harness.tts.texts == ["好的。", "请稍等"]
    assert harness.llm.calls[0] == [
        {"role": "system", "content": "你是客服"},
        {"role": "user", "content": "查订单"},
    ]
    assert len(harness.audio) == 2
    assert harness.commands == []


def test_speaking_over_the_robot_sends_break():
    async def run():
        harness = Harness(SceneConfig(scene_id="1", opening="我是智能助理，您好"))
        await harness.session.start()
        await harness.session.on_audio(VOICED * 10)
        return harness

    assert _run(run).commands == [("break", {})]


def test_transfer_waits_for_playback_then_hands_over():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", transfer_skill_group_id=7),
            asr=FakeAsr("转人工"),
            llm=FakeLlm({"text": "正在为您转接。"}, {"action": "transfer"}),
        )
        await harness.say()
        return harness

    harness = _run(run)
    assert harness.sleeps == [1.0]
    assert harness.commands == [("transfer", {"skill_group_id": 7})]
    assert harness.session.finished


def test_speaking_during_farewell_does_not_cancel_transfer():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", transfer_skill_group_id=7),
            asr=FakeAsr("转人工"),
            llm=FakeLlm({"text": "正在为您转接。"}, {"action": "transfer"}),
        )
        harness.on_sleep = lambda: harness.session.on_audio(VOICED * 10)
        await harness.say()
        return harness

    assert _run(run).commands == [("transfer", {"skill_group_id": 7})]


def test_provider_error_falls_back_per_scene():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", fallback="hangup"),
            asr=FakeAsr(error=ProviderError("asr down")),
        )
        await harness.say()
        return harness

    assert _run(run).commands == [("hangup", {})]


def test_empty_transcript_is_ignored():
    async def run():
        harness = Harness(SceneConfig(scene_id="1"), asr=FakeAsr(""))
        await harness.say()
        return harness

    harness = _run(run)
    assert harness.llm.calls == []
    assert harness.commands == []


def test_unexpected_llm_crash_falls_back_per_scene():
    async def run():
        harness = Harness(
            SceneConfig(scene_id="1", fallback="transfer", transfer_skill_group_id=7),
            asr=FakeAsr("查订单"),
            llm=CrashingLlm(),
        )
        await harness.say()
        return harness

    assert _run(run).commands == [("transfer", {"skill_group_id": 7})]


def test_unexpected_tts_crash_during_opening_falls_back_per_scene():
    async def run():
        harness = Harness(SceneConfig(scene_id="1", opening="您好", fallback="hangup"), tts=CrashingTts())
        await harness.session.start()
        return harness

    assert _run(run).commands == [("hangup", {})]
