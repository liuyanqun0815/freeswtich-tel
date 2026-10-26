from vr_engine.main import resolve_playback_path


def test_resolve_playback_path_keeps_absolute_and_stock(monkeypatch):
    monkeypatch.setenv("VR_SOUND_DIR", "/data/sounds/vr")
    assert resolve_playback_path("/usr/local/freeswitch/sounds/vr/a.wav") == (
        "/usr/local/freeswitch/sounds/vr/a.wav"
    )
    assert resolve_playback_path("ivr/ivr-welcome") == "ivr/ivr-welcome"
    assert resolve_playback_path("ivr/welcome.wav") == "ivr/welcome.wav"


def test_resolve_playback_path_maps_custom_relative(monkeypatch):
    monkeypatch.setenv("VR_SOUND_DIR", "/data/sounds/vr")
    assert resolve_playback_path("hello-abc123.wav") == "/data/sounds/vr/hello-abc123.wav"
    assert resolve_playback_path("./tone.wav") == "/data/sounds/vr/tone.wav"
    assert resolve_playback_path("") == ""
