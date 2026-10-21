import hashlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import support
import vr_config.main as config_main
from support import seed_user
from vr_api.main import app as api_app
from vr_common import redis_keys as keys
from vr_common import sipcred
from vr_common.models import Base, Extension, User
from vr_common.security import hash_password
from vr_common.sipcred import issue_sip_password, lookup_a1, store_cred
from vr_common.xmlbuild import a1_hash
from vr_config.main import app as config_app
from vr_config.main import get_db as config_get_db


def test_second_login_replaces_password():
    store = {}
    store_cred(store, "t001.vr.local", "8001", "first-password-xxxx", "A1", ttl=43200)
    store_cred(store, "t001.vr.local", "8001", "second-password-xxx", "A1", ttl=43200)
    assert lookup_a1(store, "t001.vr.local", "8001", "first-password-xxxx") is None
    assert lookup_a1(store, "t001.vr.local", "8001", "second-password-xxx")


def test_issue_sip_password_at_least_16_chars():
    assert len(issue_sip_password()) >= 16


def test_sipcred_key_shape():
    assert keys.sipcred_key("t001.vr.local", "8001") == "sipcred:t001.vr.local:8001"


@pytest.fixture
def sip_store():
    store: dict = {}
    sipcred.set_store(store)
    yield store
    sipcred.set_store({})


def _bind_extension(username: str, password: str, ext_number: str) -> User:
    assert support.SessionLocal is not None
    db = support.SessionLocal()
    try:
        ext = Extension(tenant_id="t001", ext_number=ext_number, enabled=True)
        db.add(ext)
        db.flush()
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                tenant_id="t001",
                username=username,
                password_hash=hash_password(password),
                role="agent",
                must_change_password=False,
                extension_id=ext.id,
            )
            db.add(user)
        else:
            user.extension_id = ext.id
            db.add(user)
        db.commit()
        db.refresh(user)
        return user
    finally:
        db.close()


def test_login_with_extension_returns_sip_fields(sip_store):
    seed_user("sip_agent", "secret-pass", "agent", must_change_password=False)
    _bind_extension("sip_agent", "secret-pass", "8001")

    client = TestClient(api_app)
    resp = client.post("/auth/login", json={"username": "sip_agent", "password": "secret-pass"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["extension"] == "8001"
    assert len(body["sip_password"]) >= 16
    assert body["domain"] == "t001.vr.local"
    assert body["wss_url"] == "wss://47.253.82.114:7443"
    assert body["ice_servers"] == [{"urls": "stun:stun.l.google.com:19302"}]
    assert lookup_a1(sip_store, "t001.vr.local", "8001", body["sip_password"])


def test_second_platform_login_invalidates_first_sip_password(sip_store):
    _bind_extension("sip_agent2", "secret-pass", "8002")
    client = TestClient(api_app)
    first = client.post("/auth/login", json={"username": "sip_agent2", "password": "secret-pass"})
    second = client.post("/auth/login", json={"username": "sip_agent2", "password": "secret-pass"})
    assert first.status_code == 200 and second.status_code == 200
    pw1 = first.json()["sip_password"]
    pw2 = second.json()["sip_password"]
    assert pw1 != pw2
    assert lookup_a1(sip_store, "t001.vr.local", "8002", pw1) is None
    assert lookup_a1(sip_store, "t001.vr.local", "8002", pw2)


def test_logout_deletes_sipcred(sip_store):
    _bind_extension("sip_agent3", "secret-pass", "8003")
    client = TestClient(api_app)
    login = client.post("/auth/login", json={"username": "sip_agent3", "password": "secret-pass"})
    token = login.json()["access_token"]
    pw = login.json()["sip_password"]
    assert lookup_a1(sip_store, "t001.vr.local", "8003", pw)
    logout = client.post("/auth/logout", headers={"Authorization": f"Bearer {token}"})
    assert logout.status_code == 204
    assert sipcred.get_cred(sip_store, "t001.vr.local", "8003") is None


@pytest.fixture
def config_client(monkeypatch, sip_store):
    monkeypatch.setenv("XML_CURL_USER", "fs")
    monkeypatch.setenv("XML_CURL_PASSWORD", "secret")
    monkeypatch.setenv("DEFAULT_TENANT", "t001")

    config_main._engine = None
    config_main._SessionLocal = None

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    def override_get_db():
        try:
            yield session
        finally:
            pass

    config_app.dependency_overrides[config_get_db] = override_get_db
    client = TestClient(config_app)
    client.db = session
    yield client
    config_app.dependency_overrides.clear()
    session.close()


def test_directory_without_sipcred_returns_not_found(config_client):
    ext = Extension(tenant_id="t001", ext_number="8001", enabled=True)
    config_client.db.add(ext)
    config_client.db.commit()
    response = config_client.post(
        "/fs/xml",
        data={"section": "directory", "user": "8001", "domain": "t001.vr.local"},
        auth=("fs", "secret"),
    )
    assert 'status="not found"' in response.text


def test_directory_with_sipcred_returns_a1_hash(config_client, sip_store):
    ext = Extension(tenant_id="t001", ext_number="8001", enabled=True)
    config_client.db.add(ext)
    config_client.db.flush()
    user = User(
        tenant_id="t001",
        username="dir_agent",
        password_hash="x",
        role="agent",
        must_change_password=False,
        extension_id=ext.id,
    )
    config_client.db.add(user)
    config_client.db.commit()

    password = "temp-sip-password-xx"
    a1 = a1_hash("8001", "t001.vr.local", password)
    store_cred(
        sip_store,
        "t001.vr.local",
        "8001",
        password,
        a1,
        ttl=43200,
        agent_id=str(user.id),
    )

    response = config_client.post(
        "/fs/xml",
        data={"section": "directory", "user": "8001", "domain": "t001.vr.local"},
        auth=("fs", "secret"),
    )
    assert response.status_code == 200
    assert a1 in response.text
    assert f'variable name="agent_id" value="{user.id}"' in response.text
    assert password not in response.text
    assert hashlib.md5(f"8001:t001.vr.local:{password}".encode()).hexdigest() == a1
