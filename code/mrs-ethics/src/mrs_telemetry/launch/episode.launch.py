"""One recorded episode: simulation, gate, planner and recorder, all writing to one run folder (Module 7).

  ros2 launch mrs_telemetry episode.launch.py                                  # run until Ctrl-C; send the mission yourself
  ros2 launch mrs_telemetry episode.launch.py \
      mission:="robot_a: go to room_e. robot_b: go to room_w." duration:=600  # scripted: ends when the robots
                                                                               # get a plan; 600 s at most
  ros2 launch mrs_telemetry episode.launch.py fake_script:=narrow_aisle mission:="..."   # no LLM
  ros2 launch mrs_telemetry episode.launch.py experiment:=s1 run_id:=seed03 gui:=true

The run folder (run_dir, or <results_root>/<experiment>/<run_id or date-time>) gets:
  bag/ (MCAP), gz_log/ (Gazebo state log), events.jsonl, llm_log.sqlite, run.json, ros_log/ (every node's
  log files), sim/ (generated files); check_run adds check.json.
With a mission, the episode ends by itself once the planning cycle is over: the robots got a plan
(APPROVE, or FALLBACK when none was approved; replans in between are waited for), plus end_grace_s.
end_on_plan:=false keeps it running. duration is a hard limit from the launch on, Gazebo's start-up
included. Stop with Ctrl-C (or let these end it): every process gets SIGINT, so the bag and the Gazebo
log are closed properly. Then: ros2 run mrs_telemetry check_run <run_dir>.

Module 9 (the experiment runner) will start this per scenario and seed; until then it is the way
to record a run by hand.
"""
import json
import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, EmitEvent, IncludeLaunchDescription, LogInfo, OpaqueFunction,
                            RegisterEventHandler, SetEnvironmentVariable, TimerAction)
from launch.event_handlers import OnProcessExit
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
    "end_on_plan": ("true", "with a mission: end the episode once the robots get a plan (APPROVE or FALLBACK)"),
    "end_grace_s": ("5", "seconds to keep recording after that plan"),
    "duration": ("0", "hard limit: stop after this many seconds (wall clock, Gazebo's start-up included); 0: none"),
}


def _config(package: str, name: str) -> str:
    return os.path.join(get_package_share_directory(package), "config", name)


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

    # one id for the run folder, every message (episode_id) and the LLM log; RF.safe_name keeps it a plain token
    episode_id = RF.safe_name(f"{run_dir.parent.name}_{run_dir.name}")
    # every node started below writes its ROS log files into the run folder (they hold the same lines as /rosout,
    # also from before the recorder subscribed)
    actions = [SetEnvironmentVariable("ROS_LOG_DIR", str(run_dir / RF.ROS_LOG))]
    actions += recording_actions(run_dir, robots=robots, camera=yes("camera"), scan=yes("scan"), sim_time=True,
                                 bag=True, events=events)
    RF.update_run_json(run_dir, "episode", {
        "episode_id": episode_id, "world": world, "robots": robots, "mission": get("mission"),
        "duration_s": float(get("duration")), "end_on_plan": bool(get("mission")) and yes("end_on_plan"),
        "gate": gate, "planner": planner})
    RF.update_run_json(run_dir, "sim", {"world": world, "robots": robots, "camera": yes("camera"),
                                        "gui": yes("gui"), "record_gz_log": yes("record_gz_log")})

    actions.append(_include("mrs_sim", "sim.launch.py", {
        "world": world, "robots": robots, "camera": get("camera"), "gui": get("gui"),
        "run_dir": run_dir / RF.SIM, "record_gz_log": get("record_gz_log"), "gz_log_dir": run_dir / RF.GZ_LOG}))
    if gate:
        # Launch arguments are shared across includes: an argument the gate's launch file declares
        # (params_file, defaulting to gate.yaml) would otherwise be the planner's too. So every include
        # gets its own params_file explicitly.
        actions.append(_include("mrs_ethical_gate", "gate.launch.py",
                                {"world": world, "robots": robots, "use_sim_time": "true",
                                 "params_file": _config("mrs_ethical_gate", "gate.yaml")}))
    if planner:
        planner_args = {"world": world, "robots": robots, "use_sim_time": "true",
                        "params_file": _config("mrs_llm_planner", "planner.yaml"),
                        "log_file": run_dir / RF.LLM_LOG, "run_id": run_dir.name, "episode_id": episode_id}
        for name in ("backend", "model", "fake_script"):
            if get(name):
                planner_args[name] = get(name)
        RF.update_run_json(run_dir, "planner", {k: str(v) for k, v in planner_args.items()
                                                if k not in ("world", "robots", "use_sim_time")})
        actions.append(_include("mrs_llm_planner", "planner.launch.py", planner_args))

    if get("mission"):
        # the coordinator, the telemetry node and the bag recorder must all hear the mission
        listeners = int(planner) + int(events) + 1
        end_on_plan = yes("end_on_plan")
        sender = Node(package="mrs_telemetry", executable="send_mission", name="mrs_send_mission",
                      output="screen", parameters=[{
                          "text": get("mission"), "wait_for": listeners,
                          "timeout_s": float(get("mission_timeout_s")), "use_sim_time": False,
                          "wait_for_plan": end_on_plan, "grace_s": float(get("end_grace_s"))}])
        actions.append(sender)
        if end_on_plan:
            def _ended(event, _context):
                if event.returncode != 0:   # a broken sender must not end the episode: Ctrl-C or duration will
                    return [LogInfo(msg=f"[mrs_telemetry] send_mission exited with {event.returncode}; "
                                        "the episode keeps running")]
                return [LogInfo(msg="[mrs_telemetry] the planning cycle is over: stopping the episode"),
                        EmitEvent(event=Shutdown(reason="planning cycle over"))]
            actions.append(RegisterEventHandler(OnProcessExit(target_action=sender, on_exit=_ended)))
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
