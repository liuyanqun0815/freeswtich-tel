import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import vr_config.main as config_main
from vr_common.models import Base, ConfigRelease, Extension, Gateway, User
from vr_config.main import app, get_db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("XML_CURL_USER", "fs")
    monkeypatch.setenv("XML_CURL_PASSWORD", "secret")
    monkeypatch.setenv("DEFAULT_TENANT", "t001")
    monkeypatch.setenv("RING_TIMEOUT_SECONDS", "20")
    monkeypatch.setenv("DEFAULT_SIP_PASSWORD", "sip-pw")

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

    app.dependency_overrides[get_db] = override_get_db
    test_client = TestClient(app)
    test_client.db = session
    yield test_client
    app.dependency_overrides.clear()
    session.close()


def test_fs_xml_basic_auth_failure_returns_401(client):
    response = client.post(
        "/fs/xml",
        data={"section": "directory", "user": "8001", "domain": "t001.vr.local"},
    )
    assert response.status_code == 401


def test_directory_unknown_user_returns_not_found(client):
    response = client.post(
        "/fs/xml",
        data={"section": "directory", "user": "8001", "domain": "t001.vr.local"},
        auth=("fs", "secret"),
    )
    assert response.status_code == 200
    assert 'status="not found"' in response.text


def test_directory_enabled_extension_uses_saved_db_values(client):
    ext = Extension(tenant_id="t001", ext_number="8001", enabled=True)
    user = User(
        tenant_id="t001",
        username="agent1",
        password_hash="x",
        role="agent",
        must_change_password=False,
        extension_id=1,
    )
    client.db.add(ext)
    client.db.flush()
    user.extension_id = ext.id
    client.db.add(user)
    client.db.commit()

    expected_a1 = hashlib.md5(b"8001:t001.vr.local:sip-pw").hexdigest()
    response = client.post(
        "/fs/xml",
        data={"section": "directory", "user": "8001", "domain": "t001.vr.local"},
        auth=("fs", "secret"),
    )
    assert expected_a1 in response.text
    assert f'variable name="agent_id" value="{user.id}"' in response.text


def test_dialplan_returns_unified_park_extension(client):
    response = client.post(
        "/fs/xml",
        data={
            "section": "dialplan",
            "hostname": "fs1",
            "destination_number": "4008001234",
        },
        auth=("fs", "secret"),
    )
    assert response.status_code == 200
    assert "vr_call_id=${uuid}" in response.text
    assert "vr_tenant=t001" in response.text
    assert "vr_entry=did:${destination_number}" in response.text
    assert "park_timeout=20" in response.text
    assert '<action application="park"/>' in response.text


def test_sofia_conf_returns_only_applied_snapshot_gateways(client):
    client.db.add(
        Gateway(
            name="gw-draft",
            profile="external",
            username="draft-u",
            enabled=True,
            published=False,
        )
    )
    snapshot = {
        "gateways": [
            {
                "name": "gw-published",
                "profile": "external",
                "username": "live-u",
                "password": "live-p",
                "register": True,
            }
        ]
    }
    client.db.add(
        ConfigRelease(
            tenant_id="t001",
            snapshot_json=json.dumps(snapshot),
            status="applied",
        )
    )
    client.db.commit()

    response = client.post(
        "/fs/xml",
        data={"section": "configuration", "key_value": "sofia.conf", "hostname": "fs1"},
        auth=("fs", "secret"),
    )
    assert "gw-published" in response.text
    assert "gw-draft" not in response.text


def test_sofia_conf_empty_when_no_applied_snapshot(client):
    client.db.add(
        Gateway(
            name="gw-db-only",
            profile="external",
            username="u",
            enabled=True,
            published=True,
        )
    )
    client.db.commit()

    response = client.post(
        "/fs/xml",
        data={"section": "configuration", "key_value": "sofia.conf", "hostname": "fs1"},
        auth=("fs", "secret"),
    )
    assert "gw-db-only" not in response.text
    assert "<gateways" in response.text
