"""The gate node on real topics: ProposedPlan in, EthicalVerdict (and ValidatedPlan) out.

Needs the built and sourced workspace; skipped otherwise (the skipif pattern of USER_GUIDE
section 5). On the Macs run it with pytest from a sourced terminal, not colcon test.
"""
import json
import time
from pathlib import Path

import pytest

try:
    import rclpy
    from mrs_msgs.msg import EthicalVerdict, ProposedPlan, ValidatedPlan
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter

    from mrs_common import names as N
    from mrs_common.convert import constraints_from_verdict, make_proposed_plan
    from mrs_common.qos import qos_for
    ROS_ERROR = None
except ImportError as e:
    ROS_ERROR = str(e)

from mrs_common import load_sample, plan_from_json  # noqa: E402
from mrs_common import reasons as R  # noqa: E402

from mrs_ethical_gate.settings import SettingsError  # noqa: E402

if ROS_ERROR is None:
    # Outside the try: with ROS present, a broken gate_node must fail the tests, not skip them.
    from mrs_ethical_gate.gate_node import GateNode

pytestmark = pytest.mark.skipif(
    ROS_ERROR is not None,
    reason=f"needs the built and sourced workspace (colcon build; source install/setup.zsh): {ROS_ERROR}")

PKG = Path(__file__).resolve().parents[1]
SAMPLES_WORLD = PKG.parent / "fleet_checker/test/fixtures/samples_world.yaml"
POLICY = PKG / "config" / "policy.yaml"
SAMPLES_PEOPLE = PKG / "test" / "fixtures" / "samples_world_people.yaml"


def spin_until(executor, done, timeout=5.0) -> bool:
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        executor.spin_once(timeout_sec=0.05)
    return done()


class Probe:
    """Stands in for the planner and the executor: sends ProposedPlans, collects what comes back."""

    def __init__(self):
        self.node = rclpy.create_node("gate_test_probe")
        self.verdicts, self.validated = [], []
        self.node.create_subscription(EthicalVerdict, N.VERDICT, self.verdicts.append, qos_for(N.VERDICT))
        self.node.create_subscription(ValidatedPlan, N.VALIDATED_PLAN, self.validated.append,
                                      qos_for(N.VALIDATED_PLAN))
        self.pub = self.node.create_publisher(ProposedPlan, N.PROPOSED_PLAN, qos_for(N.PROPOSED_PLAN))


@pytest.fixture
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


@pytest.fixture
def wired(ros):
    yield from _wired()


@pytest.fixture
def wired_with_a_person(ros):
    yield from _wired(Parameter("people_file", value=str(SAMPLES_PEOPLE)))


def _wired(*more):
    """The gate on samples_world, required parameters as in config/gate.yaml, and a probe."""
    gate = GateNode(parameter_overrides=[Parameter("speed", value=0.5),
                                         Parameter("crowding_radius", value=1.5),
                                         Parameter("crowding_max_robots", value=1),
                                         Parameter("map_file", value=str(SAMPLES_WORLD)),
                                         Parameter("policy_file", value=str(POLICY)), *more])
    probe = Probe()
    executor = SingleThreadedExecutor()
    executor.add_node(gate)
    executor.add_node(probe.node)
    assert spin_until(executor, lambda: probe.pub.get_subscription_count() > 0
                      and gate.verdict_pub.get_subscription_count() > 0
                      and gate.validated_pub.get_subscription_count() > 0), "nodes did not discover each other"
    yield executor, probe
    executor.shutdown()
    gate.destroy_node()
    probe.node.destroy_node()


def send(executor, probe, plan_json: str):
    """Publish plan_json as a ProposedPlan (raw, like sample_plan_pub) and wait for its verdict."""
    msg = make_proposed_plan(plan_from_json(load_sample("valid_two_robots.json")), episode_id="gate-test")
    msg.plan_json = plan_json
    probe.pub.publish(msg)
    assert spin_until(executor, lambda: any(v.transaction_id == msg.transaction_id for v in probe.verdicts))
    [verdict] = [v for v in probe.verdicts if v.transaction_id == msg.transaction_id]
    return msg, verdict


def test_an_approved_plan_gets_a_verdict_and_a_validated_plan(wired):
    executor, probe = wired
    msg, v = send(executor, probe, load_sample("valid_two_robots.json"))
    assert (v.verdict, v.episode_id, v.attempt, list(v.rule_ids)) == ("APPROVE", "gate-test", 0, [])
    assert v.latency_ms > 0
    assert spin_until(executor, lambda: any(p.transaction_id == msg.transaction_id for p in probe.validated))
    [p] = [p for p in probe.validated if p.transaction_id == msg.transaction_id]
    assert p.source_verdict == "APPROVE"
    assert plan_from_json(p.plan_json) == plan_from_json(msg.plan_json)


def test_a_rejected_plan_gets_constraints_and_no_validated_plan(wired):
    executor, probe = wired
    msg, v = send(executor, probe, load_sample("keep_clear_stop.json"))
    assert (v.verdict, list(v.rule_ids)) == ("REJECT", [R.FINAL_STOP_IN_PASSTHROUGH])
    assert {(c.kind, c.robot_id) for c in constraints_from_verdict(v)} == {("forbid_node", "robot_b")}
    spin_until(executor, lambda: False, timeout=0.5)   # time for a stray ValidatedPlan to arrive
    assert not [p for p in probe.validated if p.transaction_id == msg.transaction_id]


def test_an_unreadable_plan_gets_a_reject(wired):
    executor, probe = wired
    _, v = send(executor, probe, load_sample("invalid_bad_action.json"))
    assert (v.verdict, list(v.rule_ids)) == ("REJECT", [R.LLM_MALFORMED])


def test_a_person_from_the_people_file_is_checked_for_crowding(wired_with_a_person):
    executor, probe = wired_with_a_person
    _, v = send(executor, probe, load_sample("valid_two_robots.json"))
    assert (v.verdict, list(v.rule_ids)) == ("REJECT", [R.HUMAN_CROWDING])
    assert v.risk_score == pytest.approx(1.0)


def test_two_robots_ending_at_one_node_get_a_reject(wired):
    executor, probe = wired
    both_at_room_3 = json.dumps({"assignments": [
        {"robot_id": "robot_a", "tasks": [{"task_id": "a1", "action": "hold_position", "target": "room_3",
                                           "priority": 1}]},
        {"robot_id": "robot_b", "tasks": [{"task_id": "b1", "action": "hold_position", "target": "room_3",
                                           "priority": 1}]}]})
    _, v = send(executor, probe, both_at_room_3)
    assert (v.verdict, list(v.rule_ids)) == ("REJECT", [R.NODE_OCCUPIED])
    [c] = constraints_from_verdict(v)
    assert (c.kind, c.robot_id, c.task_id, c.node) == ("custom", "robot_a", "a1", "room_3")


def test_the_gate_does_not_start_without_a_speed(ros):
    with pytest.raises(SettingsError, match="speed is not set"):
        GateNode(parameter_overrides=[Parameter("map_file", value=str(SAMPLES_WORLD))])
