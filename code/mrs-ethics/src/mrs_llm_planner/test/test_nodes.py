"""The ROS nodes in one process: coordinator, two agents (fake LLM, scripted) and the real gate node.

Needs the built and sourced workspace; skipped otherwise (no module-level skip: see
mrs_common/test/test_roundtrip.py). On the Macs, run it with pytest from a sourced terminal:
under `colcon test` macOS strips DYLD_LIBRARY_PATH, the message type support cannot be loaded,
and the test is skipped with that reason (USER_GUIDE troubleshooting).

  source install/setup.zsh && cd src/mrs_llm_planner && python -m pytest test/test_nodes.py -v The scripted swap of config/fake_narrow_aisle.json: the
agents agree on taking turns through the aisle, the gate approves and publishes the ValidatedPlan.
"""
import time
from pathlib import Path

import pytest

try:
    import rclpy
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter
    from std_msgs.msg import String
    from mrs_msgs.msg import DialogueTurn, EthicalVerdict, ValidatedPlan

    from mrs_common import names as N
    from mrs_common.qos import qos_for
    from mrs_ethical_gate.gate_node import GateNode
    from mrs_llm_planner.agent_node import AgentNode
    from mrs_llm_planner.coordinator_node import CoordinatorNode
    ROS_ERROR = None
except ImportError as e:
    ROS_ERROR = str(e)

if ROS_ERROR is None:
    try:   # the C type support of our messages: missing under `colcon test` on macOS
        from rclpy.type_support import check_for_type_support
        check_for_type_support(DialogueTurn)
    except Exception as e:   # UnsupportedTypeSupport is not an ImportError
        ROS_ERROR = (f"message type support not loadable ({type(e).__name__}); on macOS run this test "
                     "with pytest from a sourced terminal, not colcon test")

pytestmark = pytest.mark.skipif(
    ROS_ERROR is not None,
    reason=f"needs the built and sourced workspace (colcon build; source install/setup.zsh): {ROS_ERROR}")

SCRIPT = Path(__file__).resolve().parents[1] / "config" / "fake_narrow_aisle.json"


def _params(**values):
    return [Parameter(k, value=v) for k, v in values.items()]


def test_scripted_swap_approved(tmp_path):
    rclpy.init()
    nodes = []
    try:
        common = dict(world="narrow_aisle", robots=2, backend="fake", run_id="t", use_sim_time=False)
        nodes.append(CoordinatorNode(parameter_overrides=_params(**common, episode_id="ep-nodes",
                                                                 turn_timeout_s=150.0, verdict_timeout_s=30.0)))
        for r in ("robot_a", "robot_b"):
            nodes.append(AgentNode(namespace=r, parameter_overrides=_params(
                **common, robot_id=r, fake_script_file=str(SCRIPT), log_file=str(tmp_path / "llm.sqlite"))))
        nodes.append(GateNode(parameter_overrides=_params(world="narrow_aisle", robots=2, use_sim_time=False,
                                                          speed=0.25, robot_length=0.45, crowding_radius=1.5,
                                                          crowding_max_robots=1)))
        probe = rclpy.create_node("probe")
        nodes.append(probe)
        seen = {"turns": [], "verdicts": [], "validated": []}
        probe.create_subscription(DialogueTurn, N.DIALOGUE, seen["turns"].append, qos_for(N.DIALOGUE))
        probe.create_subscription(EthicalVerdict, N.VERDICT, seen["verdicts"].append, qos_for(N.VERDICT))
        probe.create_subscription(ValidatedPlan, N.VALIDATED_PLAN, seen["validated"].append,
                                  qos_for(N.VALIDATED_PLAN))
        mission = probe.create_publisher(String, N.MISSION, qos_for(N.MISSION))

        ex = SingleThreadedExecutor()
        for n in nodes:
            ex.add_node(n)
        mission.publish(String(data="robot_a: go to the east room. robot_b: go to the west room."))
        end = time.monotonic() + 20.0
        while time.monotonic() < end and not seen["validated"]:
            ex.spin_once(timeout_sec=0.1)

        assert [v.verdict for v in seen["verdicts"]] == ["APPROVE"]
        assert seen["validated"] and seen["validated"][-1].source_verdict == "APPROVE"
        assert seen["validated"][-1].attempt == 0
        prompts = [t for t in seen["turns"] if t.kind == DialogueTurn.KIND_PROMPT]
        assert prompts[0].next_speaker == "robot_a" and "Mission:" in prompts[0].text
        assert len({t.transaction_id for t in seen["turns"]}) == 1
    finally:
        for n in nodes:
            n.destroy_node()
        rclpy.try_shutdown()
