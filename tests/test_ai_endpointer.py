from vr_ai.audio import Endpointer

VOICED = b"\x01\x00" * 160
SILENT = bytes(320)


def _endpointer(**kwargs) -> Endpointer:
    return Endpointer(is_speech=lambda frame, rate: frame[0] != 0, **kwargs)


def _kinds(events: list[tuple[str, bytes]]) -> list[str]:
    return [kind for kind, _ in events]


def test_start_after_200ms_voice_and_end_after_600ms_silence():
    endpointer = _endpointer()
    assert endpointer.push(VOICED * 9) == []
    started = _kinds(endpointer.push(VOICED))
    assert started[0] == "start"
    assert started.count("audio") == 10
    assert "end" not in _kinds(endpointer.push(SILENT * 29))
    assert _kinds(endpointer.push(SILENT))[-1] == "end"


def test_short_blip_is_ignored():
    assert _endpointer().push(VOICED * 5 + SILENT + VOICED * 5) == []


def test_long_speech_is_cut_at_max_length():
    kinds = _kinds(_endpointer(max_ms=1000).push(VOICED * 60))
    assert kinds.count("end") == 1


def test_frames_split_across_pushes():
    endpointer = _endpointer()
    assert endpointer.push(VOICED[:100]) == []
    assert _kinds(endpointer.push(VOICED[100:] + VOICED * 9))[0] == "start"


def test_real_vad_treats_silence_as_silence():
    assert Endpointer().push(SILENT * 50) == []
