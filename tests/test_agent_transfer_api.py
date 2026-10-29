from fastapi.testclient import TestClient
from sqlalchemy import select

from support import get_test_session, seed_user
from vr_api.main import app
from vr_common import sipcred
from vr_common.models import Extension, User
from vr_common.sipcred import MemoryCredStore


def test_agent_transfer_enqueues_blind_transfer_job(monkeypatch):
    jobs: list[dict] = []
    store = MemoryCredStore()
    sipcred.set_store(store)
    monkeypatch.setattr("vr_api.calls.is_registered", lambda ext: ext == "1002")

    def _enqueue(from_ext: str, *, to_ext: str | None = None, skill_group_id: int | None = None) -> None:
        jobs.append({"from_ext": from_ext, "to_ext": to_ext, "skill_group_id": skill_group_id})

    monkeypatch.setattr("vr_api.calls.enqueue_blind_transfer", _enqueue)
    try:
        seed_user("agent-xfer", "secret-pass", "agent", must_change_password=False)
        db = next(get_test_session())
        try:
            ext = Extension(ext_number="1001", display_name="坐席", enabled=True)
            db.add(ext)
            db.commit()
            db.refresh(ext)
            user = db.scalar(select(User).where(User.username == "agent-xfer"))
            assert user is not None
            user.extension_id = ext.id
            db.commit()
        finally:
            db.close()

        client = TestClient(app)
        login = client.post("/auth/login", json={"username": "agent-xfer", "password": "secret-pass"})
        assert login.status_code == 200
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        assert client.post("/agent/transfer", headers=headers, json={}).status_code == 400
        assert client.post("/agent/transfer", headers=headers, json={"target": "1001"}).status_code == 400
        assert client.post("/agent/transfer", headers=headers, json={"target": "1999"}).status_code == 409

        ok = client.post("/agent/transfer", headers=headers, json={"target": "1002"})
        assert ok.status_code == 200
        assert ok.json() == {"ok": True, "from": "1001", "target": "1002"}
        assert jobs[-1] == {"from_ext": "1001", "to_ext": "1002", "skill_group_id": None}

        from vr_common.models import SkillGroup

        db = next(get_test_session())
        try:
            group = SkillGroup(name="售后", members_json='["1002"]')
            db.add(group)
            db.commit()
            db.refresh(group)
            group_id = group.id
        finally:
            db.close()

        by_group = client.post("/agent/transfer", headers=headers, json={"skill_group_id": group_id})
        assert by_group.status_code == 200
        assert by_group.json() == {"ok": True, "from": "1001", "skill_group_id": group_id}
        assert jobs[-1] == {"from_ext": "1001", "to_ext": None, "skill_group_id": group_id}
    finally:
        sipcred.set_store(None)
