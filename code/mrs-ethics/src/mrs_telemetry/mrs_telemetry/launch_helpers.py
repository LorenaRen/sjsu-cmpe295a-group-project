"""Launch building blocks shared by record.launch.py and episode.launch.py (Module 7).
Needs the ROS 2 launch packages (not pure Python)."""
import os
from pathlib import Path

import yaml
from launch.actions import ExecuteProcess, LogInfo
from launch_ros.actions import Node

from mrs_common import names as N

from . import run_folder as RF
from .topics import qos_overrides, record_command, record_topics

TRUE = ("true", "1", "yes", "on")


def resolve_run_dir(get) -> Path:
    """The run folder from the launch arguments, made absolute (later processes may run elsewhere)."""
    path = Path(get("run_dir")) if get("run_dir") else RF.run_dir_for(get("results_root"), get("experiment"))
    return path.expanduser().resolve()


def recording_actions(run_dir: Path, *, robots: int, camera: bool, scan: bool, sim_time: bool,
                      bag: bool, events: bool) -> list:
    """The recorder and the telemetry node for one run folder (also used by episode.launch.py)."""
    ids = N.robot_ids(robots)
    topics = record_topics(ids, camera=camera, scan=scan)
    bag_dir = run_dir / RF.BAG
    if bag and bag_dir.exists():
        raise RuntimeError(f"{bag_dir} already exists: give each run its own run_dir")
    run_dir.mkdir(parents=True, exist_ok=True)
    overrides = run_dir / "qos_overrides.yaml"
    overrides.write_text(yaml.safe_dump(qos_overrides(topics), sort_keys=True))
    RF.update_run_json(run_dir, "git", RF.git_info(os.getcwd()))
    RF.update_run_json(run_dir, "recording", {
        "started": RF.now_utc(), "robots": ids, "camera": camera, "scan": scan, "sim_time": sim_time,
        "bag": bag, "events": events, "topics": topics if bag else []})
    actions = [LogInfo(msg=f"[mrs_telemetry] run folder {run_dir}")]
    if bag:
        # SIGINT first (the launch's own Ctrl-C), and long before SIGTERM / SIGKILL: closing the
        # bag writes metadata.yaml, which a killed recorder never does.
        actions.append(ExecuteProcess(cmd=record_command(bag_dir, topics, overrides_file=overrides, sim_time=sim_time),
                                      output="screen", name="mrs_bag_record",
                                      sigterm_timeout="30", sigkill_timeout="30"))
    if events:
        actions.append(Node(package="mrs_telemetry", executable="telemetry", name="mrs_telemetry", output="screen",
                            parameters=[{"events_file": str(run_dir / RF.EVENTS), "use_sim_time": sim_time}]))
    return actions
