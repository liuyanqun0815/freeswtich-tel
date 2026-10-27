import io
import wave

import numpy as np

from vr_ai.audio import StreamResampler, duration_seconds, to_wav


def test_stream_resampler_downsamples_across_odd_chunks():
    src = (np.sin(np.arange(2400) / 10) * 8000).astype(np.int16).tobytes()  # 0.1 秒 24k
    resampler = StreamResampler(24000)
    out = resampler.push(src[:1001]) + resampler.push(src[1001:]) + resampler.flush()
    assert abs(len(out) // 2 - 800) <= 16


def test_stream_resampler_passthrough_keeps_whole_samples():
    resampler = StreamResampler(8000)
    assert resampler.push(b"\x01\x02\x03") == b"\x01\x02"
    assert resampler.push(b"\x04") == b"\x03\x04"
    assert resampler.flush() == b""


def test_to_wav_and_duration():
    wav = to_wav(b"\x00\x00" * 8000)
    assert wav[:4] == b"RIFF"
    with wave.open(io.BytesIO(wav)) as reader:
        assert reader.getframerate() == 8000
        assert reader.getnframes() == 8000
    assert duration_seconds(b"\x00\x00" * 4000) == 0.5
