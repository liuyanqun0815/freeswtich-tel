"""8k telephone PCM helpers: WAV framing and resampling."""

from __future__ import annotations

import io
import wave
from collections.abc import Callable

import numpy as np
import soxr
import webrtcvad

PHONE_RATE = 8000
FRAME_MS = 20
FRAME_BYTES = PHONE_RATE * FRAME_MS // 1000 * 2


def to_wav(pcm: bytes, rate: int = PHONE_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm)
    return buf.getvalue()


def duration_seconds(pcm: bytes, rate: int = PHONE_RATE) -> float:
    return len(pcm) / (2 * rate)


class StreamResampler:
    """Resample a PCM16 byte stream to 8k across arbitrary chunk boundaries."""

    def __init__(self, src_rate: int) -> None:
        self._stream = None if src_rate == PHONE_RATE else soxr.ResampleStream(src_rate, PHONE_RATE, 1, dtype="int16")
        self._carry = b""

    def push(self, chunk: bytes) -> bytes:
        data = self._carry + chunk
        usable = len(data) - len(data) % 2
        self._carry = data[usable:]
        if self._stream is None:
            return data[:usable]
        samples = np.frombuffer(data[:usable], dtype=np.int16)
        return self._stream.resample_chunk(samples).tobytes()

    def flush(self) -> bytes:
        if self._stream is None:
            return b""
        return self._stream.resample_chunk(np.zeros(0, dtype=np.int16), last=True).tobytes()


class Endpointer:
    """Turn 8k PCM into speech start, 20ms speech frames and utterance end events."""

    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        start_ms: int = 200,
        end_ms: int = 600,
        max_ms: int = 15000,
        is_speech: Callable[[bytes, int], bool] | None = None,
    ) -> None:
        self._is_speech = is_speech or webrtcvad.Vad(aggressiveness).is_speech
        self._start_frames = start_ms // FRAME_MS
        self._end_frames = end_ms // FRAME_MS
        self._max_frames = max_ms // FRAME_MS
        self._buf = bytearray()
        self._pending: list[bytes] = []
        self._in_speech = False
        self._silent = 0
        self._frames = 0

    def push(self, pcm: bytes) -> list[tuple[str, bytes]]:
        self._buf.extend(pcm)
        events: list[tuple[str, bytes]] = []
        while len(self._buf) >= FRAME_BYTES:
            frame = bytes(self._buf[:FRAME_BYTES])
            del self._buf[:FRAME_BYTES]
            events.extend(self._frame(frame))
        return events

    def _frame(self, frame: bytes) -> list[tuple[str, bytes]]:
        voiced = self._is_speech(frame, PHONE_RATE)
        if not self._in_speech:
            if not voiced:
                self._pending = []
                return []
            self._pending.append(frame)
            if len(self._pending) < self._start_frames:
                return []
            self._in_speech = True
            self._silent = 0
            self._frames = len(self._pending)
            started = [("start", b"")] + [("audio", item) for item in self._pending]
            self._pending = []
            return started
        self._frames += 1
        self._silent = 0 if voiced else self._silent + 1
        events = [("audio", frame)]
        if self._silent >= self._end_frames or self._frames >= self._max_frames:
            self._in_speech = False
            events.append(("end", b""))
        return events
