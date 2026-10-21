import hashlib

from vr_common.xmlbuild import a1_hash, directory_xml, gateway_xml, not_found_xml, park_dialplan


def test_park_extension_sets_call_id_then_parks():
    xml = park_dialplan("t001", "did:4008001234", 20)
    assert "vr_call_id=${uuid}" in xml
    assert "vr_tenant=t001" in xml
    assert "vr_entry=did:4008001234" in xml
    assert "park_timeout=20" in xml
    assert '<action application="park"/>' in xml
    assert a1_hash("8001", "t001.vr.local", "pw") == hashlib.md5(
        b"8001:t001.vr.local:pw"
    ).hexdigest()
    assert 'status="not found"' in not_found_xml()


def test_directory_xml_uses_a1_hash_not_plain_password():
    xml = directory_xml("t001.vr.local", "8001", "abc123", "42")
    assert 'a1-hash' in xml
    assert "abc123" in xml
    assert 'name="password"' not in xml


def test_gateway_xml_escapes_special_chars():
    xml = gateway_xml(
        [
            {
                "name": "gw1",
                "profile": "external",
                "username": "u&1",
                "password": "p<2>",
                "register": True,
            }
        ]
    )
    assert "u&amp;1" in xml
    assert "p&lt;2&gt;" in xml
    assert 'name="gw1"' in xml
