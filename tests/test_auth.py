from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from support import seed_user
from vr_api.main import app
from vr_common.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_agent_cannot_create_gateway():
    client = TestClient(app)
    login = client.post("/auth/login", json={"username": "agent1", "password": "secret-pass"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    denied = client.post(
        "/gateways",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "gw"},
    )
    assert denied.status_code == 403


def test_login_wrong_password_returns_401():
    client = TestClient(app)
    resp = client.post("/auth/login", json={"username": "agent1", "password": "wrong"})
    assert resp.status_code == 401


def test_login_returns_tokens_and_must_change_password():
    client = TestClient(app)
    seed_user("admin1", "admin-pass", "admin", must_change_password=True)
    resp = client.post("/auth/login", json={"username": "admin1", "password": "admin-pass"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["must_change_password"] is True
    assert body["access_token"]
    assert body["refresh_token"]


def test_change_password_clears_must_change_password():
    client = TestClient(app)
    seed_user("user1", "old-pass", "admin", must_change_password=True)
    login = client.post("/auth/login", json={"username": "user1", "password": "old-pass"})
    token = login.json()["access_token"]
    changed = client.post(
        "/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"old_password": "old-pass", "new_password": "new-pass"},
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    relogin = client.post("/auth/login", json={"username": "user1", "password": "new-pass"})
    assert relogin.status_code == 200
    assert relogin.json()["must_change_password"] is False


def test_must_change_password_blocks_business_routes():
    client = TestClient(app)
    seed_user("locked", "secret-pass", "admin", must_change_password=True)
    login = client.post("/auth/login", json={"username": "locked", "password": "secret-pass"})
    token = login.json()["access_token"]
    blocked = client.post(
        "/gateways",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "gw"},
    )
    assert blocked.status_code == 403
    assert blocked.json() == {"detail": "password_change_required"}


def test_refresh_allowed_while_must_change_password():
    client = TestClient(app)
    seed_user("locked2", "secret-pass", "admin", must_change_password=True)
    login = client.post("/auth/login", json={"username": "locked2", "password": "secret-pass"})
    refresh_token = login.json()["refresh_token"]
    refreshed = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert refreshed.status_code == 200
    assert refreshed.json()["access_token"]


def test_admin_can_create_gateway():
    client = TestClient(app)
    seed_user("admin2", "admin-pass", "admin", must_change_password=False)
    login = client.post("/auth/login", json={"username": "admin2", "password": "admin-pass"})
    token = login.json()["access_token"]
    created = client.post(
        "/gateways",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "gw-admin"},
    )
    assert created.status_code == 201
    assert created.json()["name"] == "gw-admin"


def test_hash_and_verify_password():
    hashed = hash_password("secret-pass")
    assert hashed != "secret-pass"
    assert verify_password("secret-pass", hashed)
    assert not verify_password("wrong", hashed)


def test_access_token_contains_expected_claims(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    token = create_access_token(42, "admin", "t001")
    payload = decode_token(token)
    assert payload["sub"] == "42"
    assert payload["role"] == "admin"
    assert payload["tenant_id"] == "t001"
    assert payload["typ"] == "access"


def test_refresh_token_typ_and_expiry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    token = create_refresh_token(7)
    payload = decode_token(token)
    assert payload["sub"] == "7"
    assert payload["typ"] == "refresh"
    lifetime = payload["exp"] - payload["iat"]
    assert timedelta(hours=11, minutes=59) <= timedelta(seconds=lifetime) <= timedelta(hours=12, minutes=1)
