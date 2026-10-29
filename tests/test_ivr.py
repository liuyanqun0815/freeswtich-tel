import json

from fastapi.testclient import TestClient
from sqlalchemy import select

import support
from support import seed_user
from vr_api.main import app
from vr_common.ivr import publish, step, validate
from vr_common.models import IvrFlow, IvrPublish


def test_publish_locks_old_call_to_previous_snapshot():
    draft = {
        "id": "root",
        "kind": "menu",
        "children": [
            {"digit": "1", "id": "n1", "kind": "hangup"},
            {"digit": "1", "id": "n2", "kind": "hangup"},
        ],
    }
    assert "duplicate_digit" in validate(draft)
    ok = {
        "id": "root",
        "kind": "menu",
        "children": [{"digit": "1", "id": "n1", "kind": "queue", "target": "g1"}],
    }
    pub = publish(ok, 3)
    assert pub["version"] == 4
    assert step(pub["snapshot"], "root", "1")["target"] == "g1"


def test_step_goto_robot_child_keeps_scene_target():
    snapshot = {
        "id": "root",
        "kind": "menu",
        "children": [{"digit": "2", "id": "n1", "kind": "robot", "target": "5"}],
    }
    result = step(snapshot, "root", "2")
    assert result["action"] == "goto"
    assert result["kind"] == "robot"
    assert result["target"] == "5"
    assert result["node_id"] == "n1"


def test_star_replays_current_prompt_node():
    snapshot = {
        "id": "root",
        "kind": "menu",
        "prompt": "welcome",
        "children": [{"digit": "1", "id": "n1", "kind": "hangup"}],
    }
    result = step(snapshot, "root", "*")

    assert result["action"] == "replay"
    assert result["node_id"] == "root"


def test_hash_on_empty_collect_returns_parent():
    snapshot = {
        "id": "root",
        "kind": "menu",
        "children": [
            {
                "digit": "1",
                "id": "collect1",
                "kind": "collect",
                "terminators": ["#"],
                "parent_id": "root",
            }
        ],
    }
    result = step(snapshot, "collect1", "#", buffer="")
    assert result["action"] == "parent"
    assert result["node_id"] == "root"


def test_hash_on_nonempty_collect_submits_buffer():
    snapshot = {
        "id": "root",
        "kind": "menu",
        "children": [
            {
                "digit": "1",
                "id": "collect1",
                "kind": "collect",
                "terminators": ["#"],
                "children": [{"digit": "#", "id": "bad", "kind": "hangup"}],
            }
        ],
    }
    result = step(snapshot, "collect1", "#", buffer="13800138000")
    assert result["action"] == "complete"
    assert result["buffer"] == "13800138000"
    assert result["node_id"] == "collect1"


def test_collect_hash_parent_without_explicit_parent_id():
    snapshot = {
        "id": "root",
        "kind": "menu",
        "children": [
            {
                "digit": "1",
                "id": "collect1",
                "kind": "collect",
                "terminators": ["#"],
            }
        ],
    }
    result = step(snapshot, "collect1", "#", buffer="")
    assert result["action"] == "parent"
    assert result["node_id"] == "root"


def test_in_flight_call_keeps_locked_snapshot_after_republish():
    v1 = {
        "id": "root",
        "kind": "menu",
        "children": [{"digit": "1", "id": "n1", "kind": "queue", "target": "g1"}],
    }
    locked = publish(v1, 0)
    v2 = {
        "id": "root",
        "kind": "menu",
        "children": [{"digit": "1", "id": "n1", "kind": "queue", "target": "g2"}],
    }
    newer = publish(v2, locked["version"])
    # In-progress call keeps locked_version snapshot, not draft / newer publish
    assert step(locked["snapshot"], "root", "1")["target"] == "g1"
    assert step(newer["snapshot"], "root", "1")["target"] == "g2"
    assert locked["version"] != newer["version"]


def test_admin_publish_writes_ivr_publish_row():
    seed_user("admin-ivr", "admin-pass", "admin", must_change_password=False)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin-ivr", "password": "admin-pass"})
    token = login.json()["access_token"]

    assert support.SessionLocal is not None
    db = support.SessionLocal()
    try:
        flow = IvrFlow(
            name="main",
            draft_json=json.dumps(
                {
                    "id": "root",
                    "kind": "menu",
                    "children": [
                        {"digit": "1", "id": "n1", "kind": "queue", "target": "g1"}
                    ],
                }
            ),
        )
        db.add(flow)
        db.commit()
        db.refresh(flow)
        flow_id = flow.id
    finally:
        db.close()

    resp = client.post(
        f"/ivr/{flow_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1

    db = support.SessionLocal()
    try:
        row = db.scalars(select(IvrPublish).where(IvrPublish.flow_id == flow_id)).one()
        assert row.version == 1
        snap = json.loads(row.snapshot_json)
        assert snap["children"][0]["target"] == "g1"
    finally:
        db.close()


def test_agent_cannot_publish_ivr():
    seed_user("agent-ivr", "secret-pass", "agent", must_change_password=False)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "agent-ivr", "password": "secret-pass"})
    token = login.json()["access_token"]

    assert support.SessionLocal is not None
    db = support.SessionLocal()
    try:
        flow = IvrFlow(name="denied", draft_json="{}")
        db.add(flow)
        db.commit()
        db.refresh(flow)
        flow_id = flow.id
    finally:
        db.close()

    denied = client.post(
        f"/ivr/{flow_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 403


def test_publish_missing_flow_returns_404():
    seed_user("admin-404", "admin-pass", "admin", must_change_password=False)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin-404", "password": "admin-pass"})
    token = login.json()["access_token"]
    resp = client.post(
        "/ivr/99999/publish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_publish_invalid_draft_returns_400():
    seed_user("admin-400", "admin-pass", "admin", must_change_password=False)
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "admin-400", "password": "admin-pass"})
    token = login.json()["access_token"]

    assert support.SessionLocal is not None
    db = support.SessionLocal()
    try:
        flow = IvrFlow(
            name="bad",
            draft_json=json.dumps(
                {
                    "id": "root",
                    "kind": "menu",
                    "children": [
                        {"digit": "1", "id": "n1", "kind": "hangup"},
                        {"digit": "1", "id": "n2", "kind": "hangup"},
                    ],
                }
            ),
        )
        db.add(flow)
        db.commit()
        db.refresh(flow)
        flow_id = flow.id
    finally:
        db.close()

    resp = client.post(
        f"/ivr/{flow_id}/publish",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    assert "duplicate_digit" in resp.json()["detail"]
