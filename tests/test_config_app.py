import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import vr_config.main as config_main
from vr_common.models import Base, ConfigRelease, Entry, Extension, Gateway, PhoneNumber, User
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


def test_directory_enabled_extension_requires_sipcred(client):
    from vr_common import sipcred
    from vr_common.sipcred import MemoryCredStore
    from vr_common.xmlbuild import a1_hash

    store = MemoryCredStore()
    sipcred.set_store(store)
    try:
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

        # Without temporary SIP cred → not found (no DEFAULT_SIP_PASSWORD fallback)
        missing = client.post(
            "/fs/xml",
            data={"section": "directory", "user": "8001", "domain": "t001.vr.local"},
            auth=("fs", "secret"),
        )
        assert 'status="not found"' in missing.text

        password = "temp-sip-password-xx"
        expected_a1 = a1_hash("8001", "t001.vr.local", password)
        sipcred.store_cred(
            store,
            "t001.vr.local",
            "8001",
            password,
            expected_a1,
            ttl=43200,
            agent_id=str(user.id),
        )
        response = client.post(
            "/fs/xml",
            data={"section": "directory", "user": "8001", "domain": "t001.vr.local"},
            auth=("fs", "secret"),
        )
        assert expected_a1 in response.text
        assert f'variable name="agent_id" value="{user.id}"' in response.text
        assert hashlib.md5(b"8001:t001.vr.local:sip-pw").hexdigest() not in response.text
    finally:
        sipcred.set_store(None)


def test_dialplan_returns_unified_park_with_unknown_entry_without_db_match(client):
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
    assert "vr_entry=did:unknown" in response.text
    assert "park_timeout=20" in response.text
    assert '<action application="park"/>' in response.text


def test_dialplan_routes_enabled_extension(client):
    client.db.add(Extension(tenant_id="t001", ext_number="1000", enabled=True))
    client.db.commit()
    response = client.post(
        "/fs/xml",
        data={"section": "dialplan", "destination_number": "1000"},
        auth=("fs", "secret"),
    )
    assert response.status_code == 200
    assert "vr_entry=extension:1000" in response.text


def test_dialplan_entry_from_seeded_phone_number_and_entry(client):
    client.db.add(
        PhoneNumber(tenant_id="t001", number="4008001234", enabled=True)
    )
    client.db.add(
        Entry(
            tenant_id="t001",
            code="4008001234",
            kind="robot",
            target="scene-1",
            enabled=True,
        )
    )
    client.db.commit()

    response = client.post(
        "/fs/xml",
        data={
            "section": "dialplan",
            "hostname": "fs1",
            "Caller-Destination-Number": "4008001234",
        },
        auth=("fs", "secret"),
    )
    assert response.status_code == 200
    assert "vr_entry=robot:scene-1" in response.text
    assert "vr_tenant=t001" in response.text
    assert '<action application="park"/>' in response.text


def test_dialplan_robot_entry_uses_long_park_timeout(client, monkeypatch):
    monkeypatch.delenv("ROBOT_CALL_MAX_SECONDS", raising=False)
    client.db.add(Extension(tenant_id="t001", ext_number="1000", enabled=True))
    client.db.add(PhoneNumber(tenant_id="t001", number="4008001234", enabled=True))
    client.db.add(
        Entry(
            tenant_id="t001",
            code="4008001234",
            kind="robot",
            target="scene-1",
            enabled=True,
        )
    )
    client.db.add(PhoneNumber(tenant_id="t001", number="4008005678", enabled=True))
    client.db.commit()

    def dialplan(number):
        return client.post(
            "/fs/xml",
            data={"section": "dialplan", "destination_number": number},
            auth=("fs", "secret"),
        ).text

    robot = dialplan("4008001234")
    assert "vr_entry=robot:scene-1" in robot
    assert "park_timeout=1800" in robot
    extension = dialplan("1000")
    assert "vr_entry=extension:1000" in extension
    assert "park_timeout=20" in extension
    did = dialplan("4008005678")
    assert "vr_entry=did:4008005678" in did
    assert "park_timeout=20" in did


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


def test_acl_conf_uses_latest_applied_list_without_hiding_gateways(client):
    client.db.add(
        ConfigRelease(
            tenant_id="t001",
            snapshot_json=json.dumps(
                {"gateways": [{"name": "gw-live", "profile": "external", "register": False}]}
            ),
            status="applied",
        )
    )
    client.db.add(
        ConfigRelease(
            tenant_id="t001",
            snapshot_json=json.dumps({"acl": ["10.1.1.8"], "__release_meta__": {"kind": "acl"}}),
            status="applied",
        )
    )
    client.db.commit()

    acl = client.post(
        "/fs/xml",
        data={"section": "configuration", "key_value": "acl.conf"},
        auth=("fs", "secret"),
    )
    sofia = client.post(
        "/fs/xml",
        data={"section": "configuration", "key_value": "sofia.conf"},
        auth=("fs", "secret"),
    )
    assert 'cidr="10.1.1.8/32"' in acl.text
    assert "gw-live" in sofia.text
    assert "10.1.1.8" not in sofia.text
