from datetime import datetime
from unittest.mock import patch

from vr_common.dialer import TaskWindow, block_reason, next_action, originate, task_from_dial_task


def test_blacklist_does_not_originate():
    now = datetime(2026, 10, 21, 10, 0)
    task = TaskWindow(morning=(9, 12), afternoon=(14, 20), max_success=1)
    assert block_reason(now, task, "13800000000", {"13800000000"}, 0) == "blacklist"
    cmd = originate("gw1", "0210000", "13800000000", "c1", "u1")
    assert "origination_uuid=u1" in cmd
    assert "vr_call_id=c1" in cmd
    assert cmd.endswith("&park()")


def test_outside_window_lunch_and_night():
    task = TaskWindow()
    assert block_reason(datetime(2026, 10, 21, 13, 0), task, "13800000001", set(), 0) == "outside_window"
    assert block_reason(datetime(2026, 10, 21, 21, 0), task, "13800000001", set(), 0) == "outside_window"


def test_inside_default_windows():
    task = TaskWindow()
    assert block_reason(datetime(2026, 10, 21, 10, 0), task, "13800000001", set(), 0) is None
    assert block_reason(datetime(2026, 10, 21, 15, 0), task, "13800000001", set(), 0) is None


def test_frequency_when_success_count_reached():
    task = TaskWindow(max_success=1)
    assert block_reason(datetime(2026, 10, 21, 10, 0), task, "13800000001", set(), 1) == "frequency"
    assert block_reason(datetime(2026, 10, 21, 10, 0), task, "13800000001", set(), 2) == "frequency"


def test_next_action_blocked_has_no_originate_key():
    now = datetime(2026, 10, 21, 13, 0)
    task = TaskWindow()
    with patch("vr_common.dialer.originate") as mock_originate:
        result = next_action(
            now,
            task,
            "13800000001",
            set(),
            0,
            gateway="gw1",
            caller="0210000",
            call_id="c1",
            uuid="u1",
        )
        mock_originate.assert_not_called()
    assert result == {"status": "blocked", "reason": "outside_window"}
    assert "originate" not in result


def test_next_action_allowed_returns_originate_command():
    now = datetime(2026, 10, 21, 10, 0)
    task = TaskWindow()
    result = next_action(
        now,
        task,
        "13800000001",
        set(),
        0,
        gateway="gw1",
        caller="0210000",
        call_id="c1",
        uuid="u1",
    )
    assert result["status"] == "originate"
    assert "originate" in result
    assert "origination_uuid=u1" in result["originate"]
    assert result["originate"].endswith("&park()")


def test_task_from_dial_task_empty_window_uses_defaults():
    task = task_from_dial_task('[]', max_success_per_number_per_day=2)
    assert task.morning == (9, 12)
    assert task.afternoon == (14, 20)
    assert task.max_success == 2


def test_originate_full_bgapi_shape():
    cmd = originate("gw1", "0210000", "13800000000", "c1", "u1")
    assert (
        cmd
        == "originate {origination_uuid=u1,vr_call_id=c1,origination_caller_id_number=0210000}"
        "sofia/gateway/gw1/13800000000 &park()"
    )
