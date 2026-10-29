import json

from vr_common.blind_transfer import (
    pick_bridged_peer_uuid,
    pick_extension_leg_uuid,
    resolve_blind_transfer_target,
)


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


def test_pick_extension_leg_uuid_prefers_presence_over_caller_dest():
    """Caller dialed 1002 (dest=1002) must not be mistaken for the agent leg."""
    payload = {
        "rows": [
            {
                "uuid": "caller",
                "cid_num": "1000",
                "dest": "1002",
                "call_uuid": "call-1",
                "callstate": "ACTIVE",
            },
            {
                "uuid": "agent",
                "cid_num": "1000",
                "dest": "1002",
                "presence_id": "1002@t001.vr.local",
                "call_uuid": "call-1",
                "callstate": "ACTIVE",
            },
        ]
    }
    assert pick_extension_leg_uuid(json.dumps(payload), "1002") == "agent"


def test_pick_extension_leg_uuid_returns_none_when_missing():
    assert pick_extension_leg_uuid('{"rows":[]}', "1001") is None
    assert pick_extension_leg_uuid("not-json", "1001") is None


def test_pick_bridged_peer_uuid_returns_other_leg():
    payload = {
        "rows": [
            {"uuid": "caller", "cid_num": "1000", "call_uuid": "call-1", "callstate": "ACTIVE"},
            {
                "uuid": "agent",
                "presence_id": "1002@t001.vr.local",
                "call_uuid": "call-1",
                "callstate": "ACTIVE",
            },
        ]
    }
    assert pick_bridged_peer_uuid(json.dumps(payload), "agent") == "caller"


def test_resolve_blind_transfer_target_prefers_peer_leg():
    payload = {
        "rows": [
            {"uuid": "caller", "cid_num": "1000", "call_uuid": "call-1", "callstate": "ACTIVE"},
            {
                "uuid": "agent",
                "presence_id": "1002@t001.vr.local",
                "call_uuid": "call-1",
                "callstate": "ACTIVE",
            },
        ]
    }
    target = resolve_blind_transfer_target(json.dumps(payload), "1002")
    assert target is not None
    assert target.uuid == "caller"
    assert target.use_bleg is False


def test_resolve_blind_transfer_target_falls_back_to_bleg():
    payload = {
        "rows": [
            {
                "uuid": "agent-only",
                "presence_id": "1002@t001.vr.local",
                "callstate": "ACTIVE",
            },
        ]
    }
    target = resolve_blind_transfer_target(json.dumps(payload), "1002")
    assert target is not None
    assert target.uuid == "agent-only"
    assert target.use_bleg is True
