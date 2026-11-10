"""Test the gate's REJECT -> replan loop: the Module 5 gate and the fake planner (a development
node with no LLM, mrs_ethical_gate/dev_nodes) on the same map.

  ros2 launch mrs_ethical_gate replan_loop.launch.py scenario:=s1_passage_no_order \
      map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml
  ros2 launch mrs_ethical_gate replan_loop.launch.py scenario:=s4_end_where_b_stands \
      map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml summary_file:=/tmp/summary.json
  ros2 launch mrs_ethical_gate replan_loop.launch.py scenario:=s1_passage_no_order team_state:=b_on_edge \
      map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml

world, map_file and robots go to every node, so that they cannot work on different maps. The
gate starts through gate.launch.py, with its config/gate.yaml. Everything ends when the fake
planner has finished; its summary is the last thing it logs. No simulation is needed, so
use_sim_time defaults to false here.

team_state names a snapshot (dev_nodes/snapshots.py) for fake_team_state to publish; the gate
then takes the robots' positions from /mrs/team_state, with a margin of 3 s unless margin is
given (config/gate.yaml says why). The fake planner starts 2 s later, so that the gate has a
TeamState by then: the topic is volatile, so one published before the gate subscribed is lost.
The fake planner's FALLBACK still holds the robots at their spawn nodes.

The gate runs every rule and the fairness ledger here (rules_file defaults to config/
rules_all.yaml), since the scenarios are there to exercise them; rules_file:= another file to run
fewer, e.g. the gate's defaults with rules_file:=<share>/config/gate.yaml.
"""
from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, IncludeLaunchDescription, OpaqueFunction,
                            RegisterEventHandler, TimerAction)
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

TEAM_STATE_MARGIN = "3.0"   # [s] the error budget of team_state positions (config/gate.yaml)
PLANNER_DELAY_S = 2.0       # with team_state: time for the gate to get a TeamState first


def _nodes(context):
    launch_dir = Path(get_package_share_directory("mrs_ethical_gate")) / "launch"
    arg = LaunchConfiguration
    text = {name: ParameterValue(arg(name), value_type=str) for name in ("world", "map_file", "scenario",
                                                                          "summary_file")}
    snapshot = arg("team_state").perform(context)
    gate_args = {name: arg(name) for name in ("world", "map_file", "robots", "people_file", "use_sim_time",
                                              "rules_file")}
    if snapshot:
        gate_args.update(start_source="team_state", margin=arg("margin").perform(context) or TEAM_STATE_MARGIN)
    elif margin := arg("margin").perform(context):
        gate_args["margin"] = margin
    gate = IncludeLaunchDescription(PythonLaunchDescriptionSource(str(launch_dir / "gate.launch.py")),
                                    launch_arguments=gate_args.items())
    planner = Node(package="mrs_ethical_gate", executable="fake_planner", name="fake_planner", output="screen",
                   parameters=[{**text, "robots": arg("robots"), "max_attempts": arg("max_attempts"),
                                "use_sim_time": arg("use_sim_time")}])
    actions = [gate]
    if snapshot:
        actions += [Node(package="mrs_ethical_gate", executable="fake_team_state", name="fake_team_state",
                         output="screen", parameters=[{
                             "snapshot": snapshot, "world": text["world"], "map_file": text["map_file"],
                             "use_sim_time": arg("use_sim_time")}]),
                    TimerAction(period=PLANNER_DELAY_S, actions=[planner])]
    else:
        actions.append(planner)
    return actions + [RegisterEventHandler(OnProcessExit(target_action=planner, on_exit=[
        EmitEvent(event=Shutdown(reason="the fake planner has finished"))]))]


def generate_launch_description():
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
        DeclareLaunchArgument("rules_file", default_value=str(Path(get_package_share_directory("mrs_ethical_gate"))
                                                              / "config" / "rules_all.yaml"),
                              description="which rules the gate runs (as for gate.launch.py); default: all"),
        DeclareLaunchArgument("team_state", default_value="",
                              description="a snapshot for fake_team_state (a file, or a name in this package's "
                                          "snapshots/); empty: the gate starts robots at the spawn slots"),
        DeclareLaunchArgument("margin", default_value="",
                              description=f"the gate's margin [s]; empty: gate.yaml's, or {TEAM_STATE_MARGIN} "
                                          f"with team_state"),
        OpaqueFunction(function=_nodes),
    ])
