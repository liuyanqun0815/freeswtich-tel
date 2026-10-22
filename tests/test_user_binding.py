from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app
from vr_common.registration import MemoryRegStore, mark_registered, set_reg_store


def _admin(client: TestClient) -> dict[str, str]:
    seed_user("admin_bind", "admin-pass", "admin", must_change_password=False)
    login = client.post(
        "/auth/login", json={"username": "admin_bind", "password": "admin-pass"}
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def _extension(client: TestClient, headers: dict[str, str], number: str) -> int:
    created = client.post(
        "/extensions",
        headers=headers,
        json={"ext_number": number, "display_name": number, "enabled": True},
    )
    assert created.status_code == 201
    return created.json()["id"]


def test_user_binds_one_extension_and_can_rebind():
    client = TestClient(app)
    headers = _admin(client)
    first = _extension(client, headers, "8101")
    second = _extension(client, headers, "8102")

    created = client.post(
        "/users",
        headers=headers,
        json={
            "username": "agent_a",
            "password": "agent-pass",
            "role": "agent",
            "must_change_password": False,
            "extension_id": first,
        },
    )
    assert created.status_code == 200
    user_id = created.json()["id"]
    assert created.json()["extension_id"] == first
    assert created.json()["ext_number"] == "8101"

    taken = client.post(
        "/users",
        headers=headers,
        json={
            "username": "agent_b",
            "password": "agent-pass",
            "role": "agent",
            "extension_id": first,
        },
    )
    assert taken.status_code == 409

    missing = client.post(
        "/users",
        headers=headers,
        json={
            "username": "agent_c",
            "password": "agent-pass",
            "role": "agent",
            "extension_id": 999999,
        },
    )
    assert missing.status_code == 404

    rebound = client.put(
        f"/users/{user_id}",
        headers=headers,
        json={"extension_id": second},
    )
    assert rebound.status_code == 200
    assert rebound.json()["ext_number"] == "8102"

    listed = client.get("/users", headers=headers)
    row = next(item for item in listed.json()["items"] if item["id"] == user_id)
    assert row["ext_number"] == "8102"

    extensions = client.get("/extensions", headers=headers)
    bound = next(item for item in extensions.json()["items"] if item["id"] == second)
    assert bound["bound_username"] == "agent_a"
    free = next(item for item in extensions.json()["items"] if item["id"] == first)
    assert free["bound_username"] in (None, "")

    cleared = client.put(
        f"/users/{user_id}",
        headers=headers,
        json={"extension_id": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["extension_id"] is None
    assert cleared.json()["ext_number"] is None


def test_login_session_reports_bound_extension_status():
    client = TestClient(app)
    headers = _admin(client)
    ext_id = _extension(client, headers, "8201")
    created = client.post(
        "/users",
        headers=headers,
        json={
            "username": "seat_8201",
            "password": "seat-pass",
            "role": "agent",
            "must_change_password": False,
            "extension_id": ext_id,
        },
    )
    assert created.status_code == 200
    set_reg_store(MemoryRegStore())
    try:
        login = client.post(
            "/auth/login", json={"username": "seat_8201", "password": "seat-pass"}
        )
        assert login.status_code == 200
        assert login.json()["extension"] == "8201"
        token = login.json()["access_token"]
        seat = {"Authorization": f"Bearer {token}"}
        offline = client.get("/auth/me", headers=seat)
        assert offline.status_code == 200
        assert offline.json()["extension"] == "8201"
        assert offline.json()["registered"] is False
        mark_registered("8201", "sip:8201@10.0.0.9")
        online = client.get("/auth/me", headers=seat)
        assert online.status_code == 200
        assert online.json()["registered"] is True
        assert online.json()["contact"] == "sip:8201@10.0.0.9"
    finally:
        set_reg_store(None)
