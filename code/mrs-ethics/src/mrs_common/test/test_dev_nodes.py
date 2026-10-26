"""Development nodes: the message-building and checking logic, without spinning ROS.

Needs the built and sourced workspace; skipped otherwise (no module-level skip: see test_roundtrip.py).
"""
import pytest

try:
    from mrs_common.convert import make_proposed_plan
    from mrs_common.dev_nodes.plan_echo import describe
    ROS_ERROR = None
except ImportError as e:
    ROS_ERROR = str(e)

from mrs_common import load_sample  # noqa: E402
from mrs_common.plan_schema import plan_from_json  # noqa: E402

pytestmark = pytest.mark.skipif(
    ROS_ERROR is not None,
    reason=f"needs the built and sourced workspace (colcon build; source install/setup.zsh): {ROS_ERROR}")


def proposed(raw_json: str):
    msg = make_proposed_plan(plan_from_json(load_sample("valid_two_robots.json")), episode_id="ep-test")
    msg.plan_json = raw_json
    return msg


def test_describe_valid_plan():
    ok, text = describe(proposed(load_sample("valid_two_robots.json")))
    assert ok and text.startswith("VALID plan")
    assert "carry:room_3" in text


def test_describe_invalid_plan_points_at_the_field():
    ok, text = describe(proposed(load_sample("invalid_bad_action.json")))
    assert not ok
    assert "assignments.0.tasks.0.action" in text


def test_describe_empty_plan():
    ok, text = describe(proposed(""))
    assert not ok and "empty plan_json" in text
