"""Launch files compile everywhere; the node and the launch helpers need ROS (skipped without it)."""
import json
import py_compile
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LAUNCH = sorted((HERE.parent / "launch").glob("*.launch.py")) + [HERE.parent.parent / "mrs_sim/launch/sim.launch.py"]

try:
    import rclpy
    from mrs_msgs.msg import DialogueTurn
    from std_msgs.msg import String
    ROS_ERROR = None
except ImportError as e:
    ROS_ERROR = str(e)

needs_ros = pytest.mark.skipif(ROS_ERROR is not None, reason=f"needs the built and sourced workspace: {ROS_ERROR}")


@pytest.mark.parametrize("path", [p for p in LAUNCH if p.exists()], ids=lambda p: p.name)
def test_launch_files_compile(path, tmp_path):
    py_compile.compile(str(path), cfile=str(tmp_path / "x.pyc"), doraise=True)


@needs_ros
def test_recording_actions(tmp_path):
    from launch.actions import ExecuteProcess
    from launch_ros.actions import Node

    from mrs_telemetry.launch_helpers import recording_actions

    run = tmp_path / "run"
    actions = recording_actions(run, robots=2, camera=False, scan=True, sim_time=True, bag=True, events=True)
    assert any(isinstance(a, ExecuteProcess) for a in actions) and any(isinstance(a, Node) for a in actions)
    info = json.loads((run / "run.json").read_text())
    assert info["recording"]["robots"] == ["robot_a", "robot_b"] and (run / "qos_overrides.yaml").is_file()
    (run / "bag").mkdir()
    with pytest.raises(RuntimeError, match="already exists"):
        recording_actions(run, robots=2, camera=False, scan=True, sim_time=True, bag=True, events=True)


@needs_ros
def test_telemetry_node_writes_events(tmp_path):
    from rclpy.executors import SingleThreadedExecutor
    from rclpy.parameter import Parameter

    from mrs_common import names as N
    from mrs_common.convert import make_turn_prompt
    from mrs_common.qos import qos_for

    from mrs_telemetry.events import read_events
    from mrs_telemetry.telemetry_node import TelemetryNode

    path = tmp_path / "events.jsonl"
    rclpy.init()
    try:
        node = TelemetryNode(parameter_overrides=[Parameter("events_file", value=str(path))])
        pub_node = rclpy.create_node("test_pub")
        mission = pub_node.create_publisher(String, N.MISSION, qos_for(N.MISSION))
        dialogue = pub_node.create_publisher(DialogueTurn, N.DIALOGUE, qos_for(N.DIALOGUE))
        ex = SingleThreadedExecutor()
        ex.add_node(node)
        ex.add_node(pub_node)
        end = time.monotonic() + 5
        while dialogue.get_subscription_count() < 1 and time.monotonic() < end:
            ex.spin_once(timeout_sec=0.1)
        mission.publish(String(data="robot_a: go to room_e."))
        dialogue.publish(make_turn_prompt(episode_id="ep", transaction_id="tx", attempt=0, round=1,
                                          next_speaker="robot_a", text="Mission", node=pub_node))
        end = time.monotonic() + 5
        while node.log.counts.get("dialogue", 0) < 1 and time.monotonic() < end:
            ex.spin_once(timeout_sec=0.1)
        node.close()
        node.destroy_node()
        pub_node.destroy_node()
    finally:
        rclpy.try_shutdown()
    events, problems = read_events(path)
    types = [e["type"] for e in events]
    assert problems == [] and types[0] == "telemetry_start" and types[-1] == "telemetry_end"
    assert "mission" in types and "dialogue" in types
