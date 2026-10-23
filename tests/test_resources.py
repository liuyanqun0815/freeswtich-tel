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


def test_extension_crud():
    client = TestClient(app)
    seed_user("admin_ext", "admin-pass", "admin", must_change_password=False)
    login = client.post(
        "/auth/login", json={"username": "admin_ext", "password": "admin-pass"}
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = client.post(
        "/extensions",
        headers=headers,
        json={"ext_number": "8001", "display_name": "坐席甲", "enabled": True},
    )
    assert created.status_code == 201
    ext_id = created.json()["id"]

    again = client.post(
        "/extensions",
        headers=headers,
        json={"ext_number": "8001", "display_name": "重复"},
    )
    assert again.status_code == 409

    updated = client.put(
        f"/extensions/{ext_id}",
        headers=headers,
        json={"ext_number": "8002", "display_name": "坐席乙", "enabled": False},
    )
    assert updated.status_code == 200
    assert updated.json()["ext_number"] == "8002"
    assert updated.json()["enabled"] is False

    listed = client.get("/extensions", headers=headers)
    assert listed.status_code == 200
    assert any(item["id"] == ext_id for item in listed.json()["items"])

    removed = client.delete(f"/extensions/{ext_id}", headers=headers)
    assert removed.status_code == 204
    missing = client.delete(f"/extensions/{ext_id}", headers=headers)
    assert missing.status_code == 404


def test_gateway_and_number_crud():
    client = TestClient(app)
    seed_user("admin_line", "admin-pass", "admin", must_change_password=False)
    login = client.post(
        "/auth/login", json={"username": "admin_line", "password": "admin-pass"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created_gw = client.post(
        "/gateways",
        headers=headers,
        json={"name": "trunk-a", "proxy": "sip.example.com", "username": "acct"},
    )
    assert created_gw.status_code == 201
    gw_id = created_gw.json()["id"]
    assert created_gw.json()["published"] is False

    duplicate = client.post("/gateways", headers=headers, json={"name": "trunk-a"})
    assert duplicate.status_code == 409

    updated_gw = client.put(
        f"/gateways/{gw_id}",
        headers=headers,
        json={"name": "trunk-b", "proxy": "sip2.example.com", "enabled": False},
    )
    assert updated_gw.status_code == 200
    assert updated_gw.json()["name"] == "trunk-b"
    assert updated_gw.json()["enabled"] is False

    created_num = client.post(
        "/numbers",
        headers=headers,
        json={
            "number": "4008001234",
            "label": "客服",
            "entry_kind": "queue",
            "entry_target": "sales",
        },
    )
    assert created_num.status_code == 200
    num_id = created_num.json()["id"]
    assert created_num.json()["entry_kind"] == "queue"
    assert created_num.json()["entry_target"] == "sales"

    again = client.post("/numbers", headers=headers, json={"number": "4008001234"})
    assert again.status_code == 409

    updated_num = client.put(
        f"/numbers/{num_id}",
        headers=headers,
        json={"number": "4008009999", "label": "售后", "enabled": False},
    )
    assert updated_num.status_code == 200
    assert updated_num.json()["number"] == "4008009999"
    assert updated_num.json()["entry_kind"] is None

    listed = client.get("/numbers", headers=headers)
    assert any(item["id"] == num_id for item in listed.json()["items"])

    assert client.delete(f"/numbers/{num_id}", headers=headers).status_code == 204
    assert client.delete(f"/gateways/{gw_id}", headers=headers).status_code == 204
    assert client.delete(f"/gateways/{gw_id}", headers=headers).status_code == 404
