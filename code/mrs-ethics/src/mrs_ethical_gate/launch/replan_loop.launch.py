"""Test the gate's REJECT -> replan loop: the Module 5 gate and the fake planner (a development
node with no LLM, mrs_ethical_gate/dev_nodes) on the same map.

  ros2 launch mrs_ethical_gate replan_loop.launch.py scenario:=s1_passage_no_order \
      map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml
  ros2 launch mrs_ethical_gate replan_loop.launch.py scenario:=s4_both_hold_room_3 \
      map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml summary_file:=/tmp/summary.json

world, map_file and robots go to both nodes, so that the planner and the gate cannot work on
different maps. The gate starts through gate.launch.py, with its config/gate.yaml. Everything
ends when the fake planner has finished; its summary is the last thing it logs. No simulation
is needed, so use_sim_time defaults to false here.
"""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, IncludeLaunchDescription, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    launch_dir = Path(get_package_share_directory("mrs_ethical_gate")) / "launch"
    arg = LaunchConfiguration
    text = {name: ParameterValue(arg(name), value_type=str) for name in ("world", "map_file", "scenario",
                                                                          "summary_file")}
    gate = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(str(launch_dir / "gate.launch.py")),
        launch_arguments={name: arg(name) for name in ("world", "map_file", "robots", "people_file",
                                                       "use_sim_time")}.items())
    planner = Node(package="mrs_ethical_gate", executable="fake_planner", name="fake_planner", output="screen",
                   parameters=[{**text, "robots": arg("robots"), "max_attempts": arg("max_attempts"),
                                "use_sim_time": arg("use_sim_time")}])
    return LaunchDescription([
        DeclareLaunchArgument("scenario", description="a scenario file, or the name of one in this package's "
                                                      "scenarios/ (s1_passage_no_order, ...)"),
        DeclareLaunchArgument("world", default_value="narrow_aisle", description="as for gate.launch.py"),
        DeclareLaunchArgument("map_file", default_value="", description="as for gate.launch.py"),
        DeclareLaunchArgument("robots", default_value="2", description="as for gate.launch.py"),
        DeclareLaunchArgument("people_file", default_value="", description="as for gate.launch.py"),
        DeclareLaunchArgument("max_attempts", default_value="5",
                              description="plans to send before falling back (a placeholder)"),
        DeclareLaunchArgument("summary_file", default_value="", description="where to write the summary (JSON)"),
        DeclareLaunchArgument("use_sim_time", default_value="false",
                              description="stamp messages with simulation time"),
        gate,
        planner,
        RegisterEventHandler(OnProcessExit(target_action=planner, on_exit=[
            EmitEvent(event=Shutdown(reason="the fake planner has finished"))])),
    ])
