import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from support import get_test_session, seed_user
from vr_api.main import app
from vr_common.db import DEFAULT_TENANT
from vr_common.models import AuditLog, CallRecord, ConfigRelease
from vr_common.reconcile import reconcile
from vr_common.release import on_extension_saved, plan_apply, publish_ok, rollback


def test_failed_release_keeps_previous_applied_snapshot():
    assert plan_apply("gateway_update", "gw1")[0] == "sofia profile external killgw gw1"
    assert on_extension_saved("8001", True) == ["xml_flush_cache 8001"]
    assert on_extension_saved("8001", False) == []
    assert publish_ok(True, True) is True
    assert publish_ok(False, True) is False
    history = [
        {"id": 1, "status": "applied", "snapshot": {"gw": "old"}},
        {"id": 2, "status": "failed", "snapshot": {"gw": "new"}},
    ]
    assert rollback(history)["snapshot"]["gw"] == "old"
    assert reconcile({"c1", "c2"}, {"c2"})["missing_on_fs"] == {"c1"}


def test_plan_apply_gateway_create_and_acl():
    assert plan_apply("gateway_create", "gw2") == ["sofia profile external rescan"]
    assert plan_apply("gateway_update", "gw1") == [
        "sofia profile external killgw gw1",
        "sofia profile external rescan",
    ]
    assert plan_apply("acl", "any") == ["reloadacl"]


def test_publish_ok_without_register():
    assert publish_ok(False, False) is True
    assert publish_ok(True, False) is True


def test_post_releases_creates_pending_ignores_client_booleans():
    seed_user("admin_rel", "admin-pass", "admin", must_change_password=False)
    db = next(get_test_session())
    try:
        db.add(
            ConfigRelease(
                tenant_id=DEFAULT_TENANT,
                snapshot_json=json.dumps({"gateways": [{"name": "gw-old"}]}),
                status="applied",
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin_rel", "password": "admin-pass"})
    token = login.json()["access_token"]
    resp = client.post(
        "/releases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "kind": "gateway_update",
            "name": "gw1",
            "snapshot": {"gateways": [{"name": "gw-new"}]},
            "needs_register": True,
            "reged": False,
            "command_ok": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert body["plan"][0] == "sofia profile external killgw gw1"
    assert "id" in body

    db = next(get_test_session())
    try:
        releases = list(db.scalars(select(ConfigRelease).order_by(ConfigRelease.id)).all())
        assert [r.status for r in releases] == ["applied", "pending"]
        audits = list(db.scalars(select(AuditLog)).all())
        assert len(audits) == 1
        assert audits[0].actor == "admin_rel"
        assert audits[0].action == "publish_gateway_update"
        assert json.loads(audits[0].before_json)["gateways"][0]["name"] == "gw-old"
        assert json.loads(audits[0].after_json)["gateways"][0]["name"] == "gw-new"
    finally:
        db.close()


def test_post_releases_alone_stays_pending_not_applied():
    seed_user("admin_ok", "admin-pass", "admin", must_change_password=False)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin_ok", "password": "admin-pass"})
    token = login.json()["access_token"]
    resp = client.post(
        "/releases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "kind": "acl",
            "name": "default",
            "snapshot": {"acl": ["1.2.3.4"]},
            "needs_register": False,
            "reged": False,
            "command_ok": True,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    assert resp.json()["plan"] == ["reloadacl"]


def test_engine_apply_pending_release_success_marks_applied():
    from vr_engine.main import EngineCtx, apply_pending_release

    seed_user("admin_apply", "admin-pass", "admin", must_change_password=False)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin_apply", "password": "admin-pass"})
    token = login.json()["access_token"]
    resp = client.post(
        "/releases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "kind": "acl",
            "name": "default",
            "snapshot": {"acl": ["1.2.3.4"]},
            "needs_register": False,
        },
    )
    release_id = resp.json()["id"]
    sent: list[str] = []

    class _Transport:
        def sendall(self, data: bytes) -> None:
            sent.append(data.decode("utf-8"))

    ctx = EngineCtx(entries={}, numbers={}, transport=_Transport())
    db = next(get_test_session())
    try:
        status = apply_pending_release(
            ctx, release_id, db=db, command_ok=True, reged=False
        )
        assert status == "applied"
        row = db.get(ConfigRelease, release_id)
        assert row is not None
        assert row.status == "applied"
        assert row.applied_at is not None
        assert any("reloadacl" in line for line in sent)
    finally:
        db.close()


def test_engine_apply_pending_release_failed_keeps_previous_applied():
    from vr_engine.main import EngineCtx, apply_pending_release

    seed_user("admin_fail", "admin-pass", "admin", must_change_password=False)
    db = next(get_test_session())
    try:
        db.add(
            ConfigRelease(
                tenant_id=DEFAULT_TENANT,
                snapshot_json=json.dumps({"gateways": [{"name": "gw-old"}]}),
                status="applied",
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin_fail", "password": "admin-pass"})
    token = login.json()["access_token"]
    resp = client.post(
        "/releases",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "kind": "gateway_update",
            "name": "gw1",
            "snapshot": {"gateways": [{"name": "gw-new"}]},
            "needs_register": True,
        },
    )
    release_id = resp.json()["id"]

    class _Transport:
        def sendall(self, data: bytes) -> None:
            pass

    ctx = EngineCtx(entries={}, numbers={}, transport=_Transport())
    db = next(get_test_session())
    try:
        status = apply_pending_release(
            ctx, release_id, db=db, command_ok=True, reged=False
        )
        assert status == "failed"
        releases = list(db.scalars(select(ConfigRelease).order_by(ConfigRelease.id)).all())
        assert [r.status for r in releases] == ["applied", "failed"]
        assert json.loads(releases[0].snapshot_json)["gateways"][0]["name"] == "gw-old"
    finally:
        db.close()


def test_recording_returns_media_token_and_audits():
    seed_user("admin_rec", "admin-pass", "admin", must_change_password=False)
    db = next(get_test_session())
    try:
        db.add(
            CallRecord(
                vr_call_id="rec-1",
                tenant_id=DEFAULT_TENANT,
                record_url="/var/lib/vr/recordings/rec-1.wav",
            )
        )
        db.add(
            CallRecord(
                vr_call_id="no-rec",
                tenant_id=DEFAULT_TENANT,
                record_url=None,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin_rec", "password": "admin-pass"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    ok = client.get("/cdr/rec-1/recording", headers=headers)
    assert ok.status_code == 200
    path = ok.json()["path"]
    assert path.startswith("/media/")
    assert len(path) > len("/media/")

    missing = client.get("/cdr/no-rec/recording", headers=headers)
    assert missing.status_code == 404

    db = next(get_test_session())
    try:
        audits = list(
            db.scalars(select(AuditLog).where(AuditLog.action == "play_recording")).all()
        )
        assert len(audits) == 1
        assert audits[0].actor == "admin_rec"
    finally:
        db.close()
