"""The fake planner node and the gate node on real topics, on samples_world.

Needs the built and sourced workspace; skipped otherwise (the skipif pattern of USER_GUIDE
section 5). On the Macs run it with pytest from a sourced terminal, not colcon test.
"""
import json
import time
from pathlib import Path

import pytest

try:
    import rclpy
    from mrs_msgs.msg import EpisodeEvent, EthicalVerdict, ProposedPlan, ValidatedPlan
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter

    from mrs_common import names as N
    from mrs_common.qos import qos_for
    ROS_ERROR = None
except ImportError as e:
    ROS_ERROR = str(e)

from mrs_common import reasons as R  # noqa: E402
from mrs_common.plan_schema import hold_all_plan, plan_from_json  # noqa: E402

from mrs_ethical_gate.dev_nodes.scenarios import ScenarioError  # noqa: E402

if ROS_ERROR is None:
    # Outside the try: with ROS present, a broken node must fail the tests, not skip them.
    from mrs_ethical_gate.dev_nodes.fake_planner import EXIT_DONE, EXIT_NO_GATE, MODEL_NAME, FakePlannerNode
    from mrs_ethical_gate.gate_node import GateNode

pytestmark = pytest.mark.skipif(
    ROS_ERROR is not None,
    reason=f"needs the built and sourced workspace (colcon build; source install/setup.zsh): {ROS_ERROR}")

PKG = Path(__file__).resolve().parents[1]
SAMPLES_WORLD = PKG.parent / "fleet_checker/test/fixtures/samples_world.yaml"
NARROW_AISLE = PKG.parent / "mrs_sim/worlds/narrow_aisle.yaml"
SCENARIOS = PKG / "scenarios"
POLICY = PKG / "config" / "policy.yaml"


def spin_until(executor, done, timeout=10.0) -> bool:
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        executor.spin_once(timeout_sec=0.05)
    return done()


class Probe:
    """Listens to everything the two nodes publish."""

    def __init__(self):
        self.node = rclpy.create_node("fake_planner_test_probe")
        self.proposed, self.verdicts, self.validated, self.events = [], [], [], []
        for msg_type, topic, into in ((ProposedPlan, N.PROPOSED_PLAN, self.proposed),
                                      (EthicalVerdict, N.VERDICT, self.verdicts),
                                      (ValidatedPlan, N.VALIDATED_PLAN, self.validated),
                                      (EpisodeEvent, N.EPISODE_EVENT, self.events)):
            self.node.create_subscription(msg_type, topic, into.append, qos_for(topic))


@pytest.fixture
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


def fake_planner(scenario: str, **parameters) -> "FakePlannerNode":
    return FakePlannerNode(parameter_overrides=[
        Parameter("scenario", value=str(SCENARIOS / f"{scenario}.yaml")),
        Parameter("map_file", value=str(SAMPLES_WORLD)),
        *(Parameter(name, value=value) for name, value in parameters.items())])


@pytest.fixture
def loop_with(ros):
    """loop_with(scenario) -> (fake planner, probe): the gate on samples_world (required
    parameters as in config/gate.yaml), a probe, and the fake planner, which starts only once
    all of them are connected, so that the probe sees attempt 0 too."""
    nodes, executors = [], []

    def start(scenario: str, **parameters):
        gate = GateNode(parameter_overrides=[Parameter("speed", value=0.5),
                                             Parameter("robot_length", value=0.45),
                                             Parameter("crowding_radius", value=1.5),
                                             Parameter("crowding_max_robots", value=1),
                                             Parameter("map_file", value=str(SAMPLES_WORLD)),
                                             Parameter("policy_file", value=str(POLICY))])
        probe = Probe()
        fake = fake_planner(scenario, **parameters)
        nodes.extend([gate, probe.node, fake])
        executor = SingleThreadedExecutor()
        executors.append(executor)
        executor.add_node(gate)
        executor.add_node(probe.node)
        assert spin_until(executor, lambda: fake.proposed_pub.get_subscription_count() >= 2
                          and gate.verdict_pub.get_subscription_count() >= 2
                          and gate.validated_pub.get_subscription_count() >= 1
                          and fake.validated_pub.get_subscription_count() >= 1
                          and fake.event_pub.get_subscription_count() >= 1), "nodes did not discover each other"
        executor.add_node(fake)
        assert spin_until(executor, lambda: fake.exit_code is not None), "the fake planner did not finish"
        spin_until(executor, lambda: False, timeout=0.5)   # time for anything still on its way
        return fake, probe

    yield start
    for executor in executors:
        executor.shutdown()
    for node in nodes:
        node.destroy_node()


def test_the_s1_conflict_is_fixed_in_round_2(loop_with):
    fake, probe = loop_with("s1_passage_no_order")
    assert fake.exit_code == EXIT_DONE
    summary = fake.loop.summary()
    assert (summary["outcome"], summary["passed_in_round"]) == ("approved", 2)
    txn = fake.loop.transaction_id
    assert [(p.transaction_id, p.attempt, p.model_name) for p in probe.proposed] == [
        (txn, 0, MODEL_NAME), (txn, 1, MODEL_NAME)]
    assert probe.proposed[1].dialogue_digest == "fake planner: b2.depends_on += a1"
    assert plan_from_json(probe.proposed[1].plan_json).task("b2").depends_on == ["a1"]
    assert [(v.attempt, v.verdict, list(v.rule_ids)) for v in probe.verdicts] == [
        (0, "REJECT", [R.CAPACITY_EXCEEDED]), (1, "APPROVE", [])]
    # The gate's ValidatedPlan for attempt 1, and nothing from the fake planner.
    assert [(p.transaction_id, p.attempt, p.source_verdict) for p in probe.validated] == [(txn, 1, "APPROVE")]
    assert probe.events == []


def test_a_plan_it_cannot_fix_ends_in_the_fallback(loop_with):
    fake, probe = loop_with("s4_end_where_b_stands")
    assert fake.exit_code == EXIT_DONE
    assert fake.loop.summary()["outcome"] == "stuck"
    txn = fake.loop.transaction_id
    assert [(p.attempt, p.model_name) for p in probe.proposed] == [(0, MODEL_NAME)]   # no attempt 1
    assert [(v.attempt, v.verdict, list(v.rule_ids)) for v in probe.verdicts] == [
        (0, "REJECT", [R.NODE_OCCUPIED])]
    # Published by the fake planner, the way Module 4 is assumed to: not through the gate.
    [fallback] = probe.validated
    assert (fallback.transaction_id, fallback.attempt, fallback.source_verdict) == (txn, 0, "FALLBACK")
    assert plan_from_json(fallback.plan_json) == hold_all_plan(
        ["robot_a", "robot_b"], {"robot_a": "west_s", "robot_b": "east_n"}, R.PLANNING_EXHAUSTED)
    [event] = probe.events
    assert (event.kind, event.robot_id, event.episode_id) == ("planning_exhausted", "", fake.loop.episode_id)
    assert json.loads(event.detail_json) == {"reason": R.PLANNING_EXHAUSTED, "transaction_id": txn, "attempts": 1,
                                             "max_attempts": 5,
                                             "stopped_early": "no constraint of the REJECT could be applied"}


def test_one_attempt_is_not_enough_for_the_s1_conflict(loop_with):
    fake, probe = loop_with("s1_passage_no_order", max_attempts=1)
    assert fake.loop.summary()["outcome"] == "exhausted"
    assert [p.attempt for p in probe.proposed] == [0]
    assert [p.source_verdict for p in probe.validated] == ["FALLBACK"]


def test_the_summary_file(loop_with, tmp_path):
    fake, _ = loop_with("s1_passage_no_order", summary_file=str(tmp_path / "summary.json"))
    written = json.loads((tmp_path / "summary.json").read_text())
    assert written == json.loads(json.dumps(fake.loop.summary()))
    assert written["round_log"][0]["constraints"][0]["change"] == "b2.depends_on += a1"


def test_without_a_gate_it_gives_up(ros):
    fake = fake_planner("s1_passage_no_order", gate_wait=0.3)
    executor = SingleThreadedExecutor()
    executor.add_node(fake)
    try:
        assert spin_until(executor, lambda: fake.exit_code is not None, timeout=5.0)
        assert fake.exit_code == EXIT_NO_GATE
        assert fake.loop.rounds == [] and fake.loop.awaiting is None
    finally:
        executor.shutdown()
        fake.destroy_node()


@pytest.mark.parametrize("overrides, problem", [
    ({"scenario": ""}, "no scenario given"),
    ({"map_file": str(NARROW_AISLE)}, "written for map 'samples_world', not 'narrow_aisle'"),
])
def test_it_does_not_start_with_a_scenario_it_cannot_run(ros, overrides, problem):
    parameters = {"scenario": str(SCENARIOS / "s1_passage_no_order.yaml"), "map_file": str(SAMPLES_WORLD)}
    with pytest.raises(ScenarioError, match=problem):
        FakePlannerNode(parameter_overrides=[Parameter(k, value=v) for k, v in (parameters | overrides).items()])
