"""Module 4: the coordinator and one LLM agent per robot.

  ros2 launch mrs_llm_planner planner.launch.py                                    # ollama, phi3.5 (planner.yaml)
  ros2 launch mrs_llm_planner planner.launch.py robots:=3 backend:=anthropic model:=<model> run_id:=exp1_s01
  ros2 launch mrs_llm_planner planner.launch.py fake_script:=narrow_aisle         # scripted: APPROVE
  ros2 launch mrs_llm_planner planner.launch.py fake_script:=narrow_aisle_reject  # scripted: REJECT, all hold
  ros2 launch mrs_llm_planner planner.launch.py params_file:=/path/to/my_planner.yaml

world and robots must match sim.launch.py and gate.launch.py. Launch arguments override params_file.
"""
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

ARGS = {
    "world": ("narrow_aisle", "mrs_sim world name, or a path to a .sdf with its .yaml next to it"),
    "robots": ("2", "fleet size: robot_a, robot_b, ..."),
    "params_file": ("", "planner parameters; empty: this package's config/planner.yaml"),
    "backend": ("", "LLM backend (ollama, fake, openai, anthropic, openai_compatible); empty: params_file's"),
    "model": ("", "model name; empty: params_file's"),
    "run_id": ("", "run name in the response log; empty: params_file's"),
    "log_file": ("", "response log (SQLite); empty: params_file's"),
    "fake_script": ("", "backend fake: a config/fake_<name>.json of this package, or a path to one"),
    "use_sim_time": ("true", "use /clock from Gazebo"),
}


def _setup(context):
    share = get_package_share_directory("mrs_llm_planner")
    get = lambda name: LaunchConfiguration(name).perform(context)
    params_file = get("params_file") or os.path.join(share, "config", "planner.yaml")
    n = int(get("robots"))
    overrides = {"world": get("world"), "robots": n, "use_sim_time": get("use_sim_time").lower() == "true"}
    for name in ("backend", "model", "run_id"):
        if get(name):
            overrides[name] = get(name)
    agent_overrides = {}
    if get("log_file"):
        agent_overrides["log_file"] = get("log_file")
    script = get("fake_script")
    if script:
        if not script.endswith(".json"):
            script = os.path.join(share, "config", f"fake_{script}.json")
        agent_overrides["fake_script_file"] = script
        overrides.setdefault("backend", "fake")

    robots = [chr(ord("a") + i) for i in range(n)]
    nodes = [Node(package="mrs_llm_planner", executable="coordinator", name="mrs_coordinator", output="screen",
                  parameters=[params_file, overrides])]
    for letter in robots:
        robot_id = f"robot_{letter}"
        nodes.append(Node(package="mrs_llm_planner", executable="agent", name="mrs_agent", namespace=robot_id,
                          output="screen",
                          parameters=[params_file, {**overrides, **agent_overrides, "robot_id": robot_id}]))
    return nodes


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument(k, default_value=d, description=t) for k, (d, t) in ARGS.items()]
                             + [OpaqueFunction(function=_setup)])
