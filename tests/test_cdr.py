from datetime import datetime, timezone

from fastapi.testclient import TestClient

from support import get_test_session, seed_user
from vr_api.main import app
from vr_common.cdr import apply_event
from vr_common.db import DEFAULT_TENANT
from vr_common.models import CallRecord


def test_hangup_updates_duration_without_duplicating():
    row = apply_event({}, {"type": "start", "vr_call_id": "c1", "caller": "1000", "callee": "1001"})
    row = apply_event(row, {"type": "answer", "billsec": 0})
    row = apply_event(row, {"type": "hangup", "duration": 45, "billsec": 16, "hangup_cause": "NORMAL_CLEARING"})
    row = apply_event(row, {"type": "hangup", "duration": 45, "billsec": 16, "hangup_cause": "NORMAL_CLEARING"})
    assert row["vr_call_id"] == "c1"
    assert row["duration"] == 45
    assert row["billsec"] == 16


def test_get_cdr_summary_rates_and_fee_display():
    seed_user("admin_cdr", "admin-pass", "admin", must_change_password=False)
    db = next(get_test_session())
    try:
        db.add(
            CallRecord(
                vr_call_id="restricted-1",
                tenant_id=DEFAULT_TENANT,
                caller="1000",
                callee="1001",
                restricted=True,
            )
        )
        db.add(
            CallRecord(
                vr_call_id="answered-1",
                tenant_id=DEFAULT_TENANT,
                caller="2000",
                callee="2001",
                answered_at=datetime(2026, 10, 21, 10, 0, tzinfo=timezone.utc),
                billsec=16,
                duration=45,
            )
        )
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin_cdr", "password": "admin-pass"})
    token = login.json()["access_token"]
    resp = client.get("/cdr", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["fee_display"] == "—"
    assert body["summary"]["answer_rate"] == 0.5
    assert body["summary"]["restricted_rate"] == 0.5
