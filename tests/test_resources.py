from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app


def test_admin_can_post_blacklist_agent_forbidden():
    client = TestClient(app)
    seed_user("admin_bl", "admin-pass", "admin", must_change_password=False)

    admin_login = client.post(
        "/auth/login", json={"username": "admin_bl", "password": "admin-pass"}
    )
    assert admin_login.status_code == 200
    admin_token = admin_login.json()["access_token"]

    created = client.post(
        "/blacklist",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"phone": "13800000000", "reason": "test", "tenant_id": "other"},
    )
    assert created.status_code == 200
    body = created.json()
    assert body["phone"] == "13800000000"
    assert body.get("tenant_id", "t001") == "t001"

    agent_login = client.post(
        "/auth/login", json={"username": "agent1", "password": "secret-pass"}
    )
    assert agent_login.status_code == 200
    agent_token = agent_login.json()["access_token"]
    denied = client.post(
        "/blacklist",
        headers={"Authorization": f"Bearer {agent_token}"},
        json={"phone": "13900000000", "reason": "nope"},
    )
    assert denied.status_code == 403
