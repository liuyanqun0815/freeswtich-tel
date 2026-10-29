import json

from vr_common.blind_transfer import pick_extension_leg_uuid


def test_pick_extension_leg_uuid_prefers_latest_active_match():
    payload = {
        "row_count": 3,
        "rows": [
            {"uuid": "old", "cid_num": "1001", "dest": "4001", "callstate": "ACTIVE"},
            {"uuid": "other", "cid_num": "1002", "dest": "4001", "callstate": "ACTIVE"},
            {"uuid": "new", "cid_num": "1001", "dest": "4002", "callstate": "ACTIVE"},
        ],
    }
    assert pick_extension_leg_uuid(json.dumps(payload), "1001") == "new"


def test_pick_extension_leg_uuid_skips_non_active():
    payload = {
        "rows": [
            {"uuid": "ring", "cid_num": "1001", "dest": "4001", "callstate": "RINGING"},
            {"uuid": "ok", "presence_id": "1001@vr.local", "callstate": "ACTIVE"},
        ]
    }
    assert pick_extension_leg_uuid(json.dumps(payload), "1001") == "ok"


def test_pick_extension_leg_uuid_returns_none_when_missing():
    assert pick_extension_leg_uuid('{"rows":[]}', "1001") is None
    assert pick_extension_leg_uuid("not-json", "1001") is None
