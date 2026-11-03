"""Start the Module 5 gate (mrs_ethical_gate).

  ros2 launch mrs_ethical_gate gate.launch.py                                 # narrow_aisle, 2 robots
  ros2 launch mrs_ethical_gate gate.launch.py world:=aisle_bypass robots:=3   # same values as sim.launch.py
  ros2 launch mrs_ethical_gate gate.launch.py map_file:=/path/to/map.yaml use_sim_time:=false
  ros2 launch mrs_ethical_gate gate.launch.py people_file:=/path/to/people.yaml   # people for S-2

speed, margin and the other check settings are in config/gate.yaml (params_file:=... for
another file); the access policy and task types are in config/policy.yaml. people_file is a
launch argument, like map_file, because it belongs to the scenario; put it in no params file,
since this argument would override it.
"""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    config = Path(get_package_share_directory("mrs_ethical_gate")) / "config"
    arg = LaunchConfiguration
    return LaunchDescription([
        DeclareLaunchArgument("world", default_value="narrow_aisle",
                              description="world name in mrs_sim/worlds, or a path to a .sdf with a .yaml next to it"),
        DeclareLaunchArgument("map_file", default_value="", description="a map .yaml to use instead of world"),
        DeclareLaunchArgument("robots", default_value="2", description="number of robots (robot_a, robot_b, ...)"),
        DeclareLaunchArgument("people_file", default_value="",
                              description="people for S-2 (YAML); empty: the package's config/people.yaml (nobody)"),
        DeclareLaunchArgument("params_file", default_value=str(config / "gate.yaml"),
                              description="speed, margin and the other check settings"),
        DeclareLaunchArgument("use_sim_time", default_value="true",
                              description="stamp messages with simulation time (false without a simulation)"),
        Node(package="mrs_ethical_gate", executable="gate", name="mrs_ethical_gate", output="screen",
             parameters=[arg("params_file"), {
                 # Strings stay strings (a world called "on" or "null" would not survive YAML).
                 "world": ParameterValue(arg("world"), value_type=str),
                 "map_file": ParameterValue(arg("map_file"), value_type=str),
                 "people_file": ParameterValue(arg("people_file"), value_type=str),
                 "robots": arg("robots"),
                 "use_sim_time": arg("use_sim_time"),
             }]),
    ])
