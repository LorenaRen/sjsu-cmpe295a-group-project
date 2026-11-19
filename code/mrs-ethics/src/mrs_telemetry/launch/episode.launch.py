"""One recorded episode: simulation, gate, planner and recorder, all writing to one run folder (Module 7).

  ros2 launch mrs_telemetry episode.launch.py                                  # run until Ctrl-C; send the mission yourself
  ros2 launch mrs_telemetry episode.launch.py \
      mission:="robot_a: go to room_e. robot_b: go to room_w." duration:=240  # scripted: sends it, stops after 240 s
  ros2 launch mrs_telemetry episode.launch.py fake_script:=narrow_aisle mission:="..." duration:=60   # no LLM
  ros2 launch mrs_telemetry episode.launch.py experiment:=s1 run_id:=seed03 gui:=true

The run folder (run_dir, or <results_root>/<experiment>/<run_id or date-time>) gets:
  bag/ (MCAP), gz_log/ (Gazebo state log), events.jsonl, llm_log.sqlite, run.json, sim/ (generated files).
Stop with Ctrl-C (or let duration end it): every process gets SIGINT, so the bag and the Gazebo
log are closed properly. Then: ros2 run mrs_telemetry check_run <run_dir>.

Module 9 (the experiment runner) will start this per scenario and seed; until then it is the way
to record a run by hand.
"""
import json
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, IncludeLaunchDescription, LogInfo, OpaqueFunction,
                            TimerAction)
from launch.events import Shutdown
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from mrs_telemetry import run_folder as RF
from mrs_telemetry.launch_helpers import TRUE, recording_actions, resolve_run_dir

ARGS = {
    "run_dir": ("", "the run folder; empty: <results_root>/<experiment>/<run_id>"),
    "results_root": ("results", "where run folders go"),
    "experiment": ("dev", "experiment name (a folder under results_root)"),
    "run_id": ("", "run name; empty: the date and time"),
    "world": ("narrow_aisle", "mrs_sim world"),
    "robots": ("2", "fleet size"),
    "camera": ("false", "robot cameras (simulated and recorded)"),
    "gui": ("false", "open the Gazebo GUI"),
    "record_gz_log": ("true", "write the Gazebo state log"),
    "scan": ("true", "record the LiDAR scans"),
    "gate": ("true", "start the gate (Module 5)"),
    "planner": ("true", "start the planner (Module 4)"),
    "backend": ("", "planner LLM backend; empty: planner.yaml's (ollama)"),
    "model": ("", "planner model; empty: planner.yaml's (phi3.5)"),
    "fake_script": ("", "planner backend fake with a scripted dialogue, e.g. narrow_aisle"),
    "mission": ("", "mission text to send; empty: send it yourself (ros2 topic pub ...)"),
    "mission_timeout_s": ("90", "how long send_mission waits for the planner and recorders to subscribe"),
    "duration": ("0", "stop the episode after this many seconds (wall clock); 0: run until Ctrl-C"),
}


def _include(package: str, launch_file: str, args: dict) -> IncludeLaunchDescription:
    path = os.path.join(get_package_share_directory(package), "launch", launch_file)
    return IncludeLaunchDescription(PythonLaunchDescriptionSource(path),
                                    launch_arguments={k: str(v) for k, v in args.items()}.items())


def _setup(context):
    get = lambda name: LaunchConfiguration(name).perform(context)  # noqa: E731
    yes = lambda name: get(name).lower() in TRUE  # noqa: E731
    if get("run_dir"):
        run_dir = resolve_run_dir(get)
    else:
        run_dir = RF.run_dir_for(get("results_root"), get("experiment"), get("run_id")).expanduser().resolve()
    robots, world = int(get("robots")), get("world")
    planner, gate, events = yes("planner"), yes("gate"), True

    actions = recording_actions(run_dir, robots=robots, camera=yes("camera"), scan=yes("scan"), sim_time=True,
                                bag=True, events=events)
    RF.update_run_json(run_dir, "episode", {
        "world": world, "robots": robots, "mission": get("mission"), "duration_s": float(get("duration")),
        "gate": gate, "planner": planner})
    RF.update_run_json(run_dir, "sim", {"world": world, "robots": robots, "camera": yes("camera"),
                                        "gui": yes("gui"), "record_gz_log": yes("record_gz_log")})

    actions.append(_include("mrs_sim", "sim.launch.py", {
        "world": world, "robots": robots, "camera": get("camera"), "gui": get("gui"),
        "run_dir": run_dir / RF.SIM, "record_gz_log": get("record_gz_log"), "gz_log_dir": run_dir / RF.GZ_LOG}))
    if gate:
        actions.append(_include("mrs_ethical_gate", "gate.launch.py",
                                {"world": world, "robots": robots, "use_sim_time": "true"}))
    if planner:
        planner_args = {"world": world, "robots": robots, "use_sim_time": "true",
                        "log_file": run_dir / RF.LLM_LOG, "run_id": run_dir.name}
        for name in ("backend", "model", "fake_script"):
            if get(name):
                planner_args[name] = get(name)
        RF.update_run_json(run_dir, "planner", {k: str(v) for k, v in planner_args.items()
                                                if k not in ("world", "robots", "use_sim_time")})
        actions.append(_include("mrs_llm_planner", "planner.launch.py", planner_args))

    if get("mission"):
        # the coordinator, the telemetry node and the bag recorder must all hear the mission
        listeners = int(planner) + int(events) + 1
        actions.append(Node(package="mrs_telemetry", executable="send_mission", name="mrs_send_mission",
                            output="screen", parameters=[{
                                "text": get("mission"), "wait_for": listeners,
                                "timeout_s": float(get("mission_timeout_s")), "use_sim_time": False}]))
        actions.append(LogInfo(msg=f"[mrs_telemetry] mission to send: {json.dumps(get('mission'))}"))
    duration = float(get("duration"))
    if duration > 0:
        actions.append(TimerAction(period=duration, actions=[
            LogInfo(msg=f"[mrs_telemetry] {duration:.0f} s are up: stopping the episode"),
            EmitEvent(event=Shutdown(reason="episode duration reached"))]))
    actions.append(LogInfo(msg=f"[mrs_telemetry] when it ends: ros2 run mrs_telemetry check_run {run_dir}"))
    return actions


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument(k, default_value=d, description=t) for k, (d, t) in ARGS.items()]
                             + [OpaqueFunction(function=_setup)])
