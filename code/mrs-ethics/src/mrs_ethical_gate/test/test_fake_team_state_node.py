"""fake_team_state on real topics: a snapshot file in, TeamState messages out, read by the gate.

Needs the built and sourced workspace; skipped otherwise (the skipif pattern of USER_GUIDE
section 5). On the Macs run it with pytest from a sourced terminal, not colcon test.
"""
import time
from pathlib import Path

import pytest

try:
    import rclpy
    from mrs_msgs.msg import TeamState
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter

    from mrs_common import names as N
    from mrs_common.qos import qos_for
    ROS_ERROR = None
except ImportError as e:
    ROS_ERROR = str(e)

from mrs_common import load_sample  # noqa: E402

from mrs_ethical_gate.dev_nodes.scenarios import ScenarioError  # noqa: E402

if ROS_ERROR is None:
    # Outside the try: with ROS present, a broken node must fail the tests, not skip them.
    from mrs_ethical_gate.dev_nodes.fake_team_state import EXIT_DONE, FakeTeamStateNode
    from mrs_ethical_gate.gate_node import GateNode

pytestmark = pytest.mark.skipif(
    ROS_ERROR is not None,
    reason=f"needs the built and sourced workspace (colcon build; source install/setup.zsh): {ROS_ERROR}")

PKG = Path(__file__).resolve().parents[1]
SAMPLES_WORLD = PKG.parent / "fleet_checker/test/fixtures/samples_world.yaml"


def spin_until(executor, done, timeout=5.0) -> bool:
    end = time.monotonic() + timeout
    while not done() and time.monotonic() < end:
        executor.spin_once(timeout_sec=0.02)
    return done()


@pytest.fixture
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


def feed(*more):
    return FakeTeamStateNode(parameter_overrides=[Parameter("map_file", value=str(SAMPLES_WORLD)), *more])


def listen(executor):
    node = rclpy.create_node("team_state_listener")
    got = []
    node.create_subscription(TeamState, N.TEAM_STATE, got.append, qos_for(N.TEAM_STATE))
    executor.add_node(node)
    return node, got


def test_it_publishes_the_snapshot_stamped_now(ros):
    fake = feed(Parameter("snapshot", value="b_on_edge"), Parameter("rate", value=20.0),
                Parameter("episode_id", value="ep7"))
    executor = SingleThreadedExecutor()
    executor.add_node(fake)
    listener, got = listen(executor)
    assert spin_until(executor, lambda: len(got) >= 2)
    msg = got[-1]
    assert (msg.episode_id, list(msg.blocked_edges)) == ("ep7", [])
    assert [(r.robot_id, r.episode_id, r.at, r.x, r.y) for r in msg.robots] == [
        ("robot_a", "ep7", "west_s", 0.0, -1.2), ("robot_b", "ep7", "corridor_1->pass_e", 8.6, 0.0)]
    assert msg.robots[1].yaw == pytest.approx(3.14159)
    stamp = msg.stamp.sec + msg.stamp.nanosec * 1e-9
    assert abs(stamp - time.time()) < 5.0                       # the node clock: wall time here
    assert all(r.stamp == msg.stamp for r in msg.robots)
    executor.shutdown()
    fake.destroy_node()
    listener.destroy_node()


def test_it_stops_after_stop_after(ros):
    fake = feed(Parameter("snapshot", value="at_spawns"), Parameter("rate", value=20.0),
                Parameter("stop_after", value=0.3))
    executor = SingleThreadedExecutor()
    executor.add_node(fake)
    listener, got = listen(executor)
    assert spin_until(executor, lambda: fake.exit_code is not None)
    assert fake.exit_code == EXIT_DONE
    sent = len(got)
    spin_until(executor, lambda: False, timeout=0.3)
    assert len(got) == sent and fake.sent >= 1
    executor.shutdown()
    fake.destroy_node()
    listener.destroy_node()


def test_a_snapshot_it_cannot_use_stops_it(ros):
    with pytest.raises(ScenarioError, match="no snapshot given"):
        feed()


def test_the_gate_checks_plans_where_it_says_the_robots_are(ros):
    from mrs_msgs.msg import EthicalVerdict, ProposedPlan
    from mrs_common.convert import make_proposed_plan
    from mrs_common import plan_from_json

    gate = GateNode(parameter_overrides=[
        Parameter("speed", value=0.5), Parameter("robot_length", value=0.45), Parameter("crowding_radius", value=1.5),
        Parameter("crowding_max_robots", value=1), Parameter("map_file", value=str(SAMPLES_WORLD)),
        Parameter("start_source", value="team_state"), Parameter("team_state_max_age", value=1.0)])
    fake = feed(Parameter("snapshot", value="b_in_passage"), Parameter("rate", value=10.0))
    probe = rclpy.create_node("plan_probe")
    verdicts = []
    probe.create_subscription(EthicalVerdict, N.VERDICT, verdicts.append, qos_for(N.VERDICT))
    pub = probe.create_publisher(ProposedPlan, N.PROPOSED_PLAN, qos_for(N.PROPOSED_PLAN))
    executor = SingleThreadedExecutor()
    for node in (gate, fake, probe):
        executor.add_node(node)
    assert spin_until(executor, lambda: gate.session.starts.latest is not None and pub.get_subscription_count() > 0
                      and gate.verdict_pub.get_subscription_count() > 0)
    pub.publish(make_proposed_plan(plan_from_json(load_sample("valid_two_robots.json")), episode_id="ep1"))
    assert spin_until(executor, lambda: verdicts)
    assert (verdicts[0].verdict, list(verdicts[0].rule_ids)) == ("REJECT", ["capacity_exceeded"])
    executor.shutdown()
    for node in (gate, fake, probe):
        node.destroy_node()
