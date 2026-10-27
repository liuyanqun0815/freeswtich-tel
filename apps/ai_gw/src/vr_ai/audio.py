"""8k telephone PCM helpers: WAV framing and resampling."""

from __future__ import annotations

import io
import wave

import numpy as np
import soxr

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
