from pathlib import Path

from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app


def test_admin_can_upload_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("VR_SOUND_DIR", str(tmp_path))
    client = TestClient(app)
    seed_user("admin_media", "admin-pass", "admin", must_change_password=False)
    login = client.post(
        "/auth/login", json={"username": "admin_media", "password": "admin-pass"}
    )
    token = login.json()["access_token"]

    resp = client.post(
        "/media/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("welcome.wav", b"RIFF....WAVE", "audio/wav")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"].endswith(".wav")
    assert Path(body["path"]).is_file()
    assert Path(body["path"]).read_bytes().startswith(b"RIFF")


def test_reject_non_audio_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("VR_SOUND_DIR", str(tmp_path))
    client = TestClient(app)
    seed_user("admin_media2", "admin-pass", "admin", must_change_password=False)
    login = client.post(
        "/auth/login", json={"username": "admin_media2", "password": "admin-pass"}
    )
    token = login.json()["access_token"]

    resp = client.post(
        "/media/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400
