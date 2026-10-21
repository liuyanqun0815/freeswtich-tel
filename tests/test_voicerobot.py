import base64

from vr_common.voicerobot import from_voice_robot, to_voice_robot


def test_transfer_marker_becomes_action():
    out = from_voice_robot({"type": "assistant_text", "text": "好的[[transfer]]"})
    assert out["type"] == "action.transfer"
    assert "[[transfer]]" not in out.get("text", "")


def test_from_voice_robot_unknown_type_returns_none():
    assert from_voice_robot({"type": "unknown", "text": "hi"}) is None


def test_to_voice_robot_audio_chunk_base64():
    pcm = b"\x00\x01\x02\x03"
    out = to_voice_robot(pcm, 8000)
    assert out["type"] == "audio_chunk"
    assert out["audio"] == base64.b64encode(pcm).decode("ascii")
    assert out["sample_rate"] == 8000
