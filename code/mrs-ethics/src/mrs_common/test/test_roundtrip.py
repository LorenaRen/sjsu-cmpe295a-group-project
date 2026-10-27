"""Module 1 "done when": a TeamPlan survives ProposedPlan -> CDR bytes -> ProposedPlan without loss.

Needs the built workspace (mrs_msgs + rclpy). Skipped automatically in a plain Python env;
run with `colcon test --packages-select mrs_common` or `pytest` after sourcing install/setup.*.

Do NOT use pytest.skip()/importorskip() at module level in this repo: ROS's launch_testing
pytest plugin imports test files during collection, and a module-level skip there skips the
whole test/ folder ("collected 0 items / 1 skipped"). Mark the tests with skipif instead.
"""
import pytest

try:
    from rclpy.serialization import deserialize_message, serialize_message

    from mrs_common.convert import (constraints_from_verdict, make_episode_event, make_proposed_plan,
                                    make_task_status, make_validated_plan, make_verdict, plan_from_msg)
    from mrs_msgs.msg import EpisodeEvent, EthicalVerdict, ProposedPlan, TeamState, ValidatedPlan
    ROS_ERROR = None
except ImportError as e:  # workspace not built / not sourced
    ROS_ERROR = str(e)

from mrs_common import load_sample  # noqa: E402
from mrs_common.ids import is_transaction_id  # noqa: E402
from mrs_common.plan_schema import Constraint, plan_from_json  # noqa: E402

pytestmark = pytest.mark.skipif(
    ROS_ERROR is not None,
    reason=f"needs the built and sourced workspace (colcon build; source install/setup.zsh): {ROS_ERROR}")


def wire(msg):
    """Serialise exactly as DDS would and read it back."""
    return deserialize_message(serialize_message(msg), type(msg))


@pytest.mark.parametrize("sample", ["valid_two_robots.json", "exit_blocking.json"])
def test_team_plan_round_trip_through_proposed_plan(sample):
    plan = plan_from_json(load_sample(sample))
    msg = make_proposed_plan(plan, episode_id="ep-test", attempt=1, model_name="fake",
                             latency_ms=1234.5, tokens_in=900, tokens_out=150, dialogue_digest="2 rounds")
    back = wire(msg)
    assert isinstance(back, ProposedPlan)
    assert plan_from_msg(back) == plan                       # the plan itself: no loss
    assert back.transaction_id == msg.transaction_id and is_transaction_id(back.transaction_id)
    assert (back.episode_id, back.attempt, back.model_name) == ("ep-test", 1, "fake")
    assert (back.latency_ms, back.tokens_in, back.tokens_out) == (1234.5, 900, 150)
    assert back.stamp.sec == msg.stamp.sec


def test_transaction_id_flows_to_verdict_and_validated_plan():
    plan = plan_from_json(load_sample("exit_blocking.json"))
    proposed = wire(make_proposed_plan(plan, episode_id="ep-test"))
    c = Constraint(kind="replace_target", rule_id="S1_exit_clearance", robot_id="robot_b", task_id="b1",
                   value="corridor_2", text="Park at corridor_2 instead of exit_A.")
    verdict = wire(make_verdict(transaction_id=proposed.transaction_id, episode_id="ep-test", attempt=0,
                                verdict=EthicalVerdict.VERDICT_MODIFY, rule_ids=["S1_exit_clearance"],
                                risk_score=1.7, rationale="robot_b would block exit_A", constraints=[c]))
    assert verdict.transaction_id == proposed.transaction_id
    assert verdict.risk_score == pytest.approx(1.0)          # clamped to [0, 1]
    assert constraints_from_verdict(verdict) == [c]
    validated = wire(make_validated_plan(plan, transaction_id=proposed.transaction_id, episode_id="ep-test",
                                         attempt=0, source_verdict="MODIFY"))
    assert isinstance(validated, ValidatedPlan) and plan_from_msg(validated) == plan


def test_builders_reject_bad_enums():
    with pytest.raises(ValueError):
        make_verdict(transaction_id="t", episode_id="e", attempt=0, verdict="MAYBE")
    with pytest.raises(ValueError):
        make_task_status(transaction_id="t", episode_id="e", robot_id="robot_a", task_id="a1", state="flying")
    with pytest.raises(ValueError):
        make_episode_event(episode_id="e", kind="explosion")


def test_corrupted_plan_json_is_detected():
    msg = make_proposed_plan(plan_from_json(load_sample("valid_two_robots.json")), episode_id="e")
    msg.plan_json = msg.plan_json.replace('"navigate"', '"fly"')
    with pytest.raises(Exception):
        plan_from_msg(wire(msg))


def test_blocked_edges_round_trip_and_event():
    import json

    from mrs_common import names as N
    from mrs_common import reasons as R

    edge = N.edge_id("room_3", "corridor_1")
    back = wire(TeamState(episode_id="ep-test", blocked_edges=[edge]))
    assert list(back.blocked_edges) == ["corridor_1--room_3"]
    ev = wire(make_episode_event(episode_id="ep-test", kind=EpisodeEvent.KIND_EDGE_BLOCKED, robot_id="robot_a",
                                 detail=R.detail(R.EDGE_BLOCKED, edge=edge)))
    assert ev.kind == "edge_blocked" and json.loads(ev.detail_json)["edge"] == edge
    assert make_episode_event(episode_id="e", kind="edge_cleared").kind == EpisodeEvent.KIND_EDGE_CLEARED
