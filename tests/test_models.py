from vr_common.models import CallRecord, User


def test_call_record_unique_and_tenant_default():
    table = CallRecord.__table__
    assert table.c.vr_call_id.unique
    assert table.c.tenant_id.default.arg == "t001"
    assert "billsec" in table.c
    assert "duration" in table.c
    assert "queue_sec" in table.c
    assert "robot_sec" in table.c
    assert "agent_billsec" in table.c
    assert User.__table__.c.password_hash.nullable is False
    assert User.__table__.c.must_change_password.nullable is False
