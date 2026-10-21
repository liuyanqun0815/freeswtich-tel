import pytest

from vr_common.protocol import (
    commands_for_cancel,
    commands_for_transfer,
    degraded,
    parse_message,
    reject_keyword_hit,
)


def test_unknown_type_ignored():
    assert parse_message('{"type":"nope","session_id":"c","seq":1,"ts":1}') is None


def test_session_start_valid():
    raw = '{"type":"session.start","session_id":"c","seq":1,"ts":1,"sample_rate":8000}'
    msg = parse_message(raw)
    assert msg is not None
    assert msg["type"] == "session.start"
    assert msg["sample_rate"] == 8000


def test_session_start_rejects_non_8000_sample_rate():
    raw = '{"type":"session.start","session_id":"c","seq":1,"ts":1,"sample_rate":16000}'
    with pytest.raises(ValueError):
        parse_message(raw)


def test_silence_degrades_and_reject_keyword_hits():
    assert degraded(3000, False) is True
    assert degraded(2999, False) is False
    assert degraded(3000, True) is False
    assert reject_keyword_hit("不要再打了", ["不要再打"]) is True


def test_commands_for_cancel_and_transfer():
    cancel = commands_for_cancel("u1")
    assert len(cancel) == 1
    assert cancel[0].api == "uuid_break"
    assert cancel[0].args == "u1 all"

    transfer = commands_for_transfer("u1")
    assert transfer[0].api == "uuid_audio_stream"
    assert transfer[0].args == "u1 stop"
