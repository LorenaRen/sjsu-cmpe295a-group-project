"""Module 7: record one run into a run folder (MCAP bag + events.jsonl + run.json).

  ros2 launch mrs_telemetry record.launch.py                               # results/dev/<time>/, 2 robots
  ros2 launch mrs_telemetry record.launch.py run_dir:=results/s1/run_03 robots:=3
  ros2 launch mrs_telemetry record.launch.py camera:=true scan:=false     # images on, scans off
  ros2 launch mrs_telemetry record.launch.py sim_time:=false              # no Gazebo running

Start it with (or from) the launch that runs the episode, before the mission is sent:
episode.launch.py includes it. Stop it with Ctrl-C (SIGINT): the recorder then closes the bag
and writes metadata.yaml. A recorder that is killed leaves a bag without metadata.yaml, which
`ros2 bag reindex <run_dir>/bag` repairs. Afterwards: ros2 run mrs_telemetry check_run <run_dir>.

robots must match the simulation's. With sim_time (the default) nothing reaches the bag before
Gazebo's first /clock.
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration

from mrs_telemetry.launch_helpers import TRUE, recording_actions, resolve_run_dir

ARGS = {
    "run_dir": ("", "the run folder; empty: <results_root>/<experiment>/<date-time>"),
    "results_root": ("results", "where run folders go when run_dir is empty"),
    "experiment": ("dev", "experiment name (a folder under results_root)"),
    "robots": ("2", "fleet size, as in sim.launch.py"),
    "camera": ("false", "also record the camera images (large)"),
    "scan": ("true", "record the LiDAR scans"),
    "sim_time": ("true", "stamp the bag and events in simulation time (false when no Gazebo runs)"),
    "bag": ("true", "record the MCAP bag"),
    "events": ("true", "write events.jsonl (telemetry node)"),
}


def _setup(context):
    get = lambda name: LaunchConfiguration(name).perform(context)  # noqa: E731
    yes = lambda name: get(name).lower() in TRUE  # noqa: E731
    return recording_actions(resolve_run_dir(get), robots=int(get("robots")), camera=yes("camera"),
                             scan=yes("scan"), sim_time=yes("sim_time"), bag=yes("bag"), events=yes("events"))


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument(k, default_value=d, description=t) for k, (d, t) in ARGS.items()]
                             + [OpaqueFunction(function=_setup)])
