"""What goes into the bag, and how the recorder subscribes (Module 7). Pure Python.

The topic list comes from mrs_common.names, so a renamed topic is recorded without touching this
package. Topics are listed explicitly (not by regex): the recorder subscribes to each as soon as it
appears, and check_run can tell a topic nobody published from one the recorder missed.

  record_topics(["robot_a", "robot_b"])            # every canonical topic + /clock, /tf, /tf_static
  record_command(run_dir, topics, overrides_file)   # the ros2 bag record command line
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

from mrs_common import names as N
from mrs_common.qos import LATCHED_TOPICS

TF = "/tf"
TF_STATIC = "/tf_static"

# Who publishes each topic, so check_run can say why one is empty. Robot topics by leaf name.
PUBLISHER = {
    N.CLOCK: "Gazebo (Module 2)", TF: "Gazebo (Module 2)", TF_STATIC: "sim.launch.py (Module 2)",
    N.SCAN: "Gazebo (Module 2)", N.ODOM: "Gazebo (Module 2)", N.GROUND_TRUTH: "Gazebo (Module 2)",
    N.CAMERA_IMAGE: "Gazebo (Module 2, camera:=true)", N.CAMERA_INFO: "Gazebo (Module 2, camera:=true)",
    N.WORLD_STATE: "world state (Module 3)", N.TEAM_STATE: "world state (Module 3)",
    N.MISSION: "the mission sender (episode.launch.py, Module 9, or ros2 topic pub)",
    N.DIALOGUE: "planner (Module 4)", N.PROPOSED_PLAN: "planner (Module 4)",
    N.VERDICT: "gate (Module 5)", N.VALIDATED_PLAN: "gate or planner FALLBACK (Modules 4-5)",
    N.CMD_VEL_NOMINAL: "executor (Module 6)", N.CMD_VEL: "CBF filter (Module 6)",
    N.SAFETY_OVERRIDE: "CBF filter (Module 6)", N.TASK_STATUS: "executor (Module 6)",
    N.EPISODE_EVENT: "planner, runner, monitors (Modules 4, 9)", N.MARKERS: "visualisation (Module 8)",
}

# Topics a run of the current stack (sim + planner + gate) must have messages on. The others
# stay empty until their module exists; check_run reports them, but does not fail on them.
REQUIRED_TEAM = (N.CLOCK, TF_STATIC, N.MISSION, N.DIALOGUE)
REQUIRED_ROBOT = (N.SCAN, N.ODOM, N.GROUND_TRUTH)


def publisher_of(topic: str) -> str:
    if topic in PUBLISHER:
        return PUBLISHER[topic]
    leaf = topic.split("/", 2)[-1] if topic.count("/") >= 2 else topic
    return PUBLISHER.get(leaf, "unknown")


def record_topics(robots: Sequence[str], *, camera: bool = False, scan: bool = True,
                  extra: Iterable[str] = ()) -> list[str]:
    """Every topic to record, sorted. camera: the camera images (large); scan: the LiDAR scans."""
    topics = set(N.all_topics(robots, camera=camera)) | {N.CLOCK, TF, TF_STATIC} | set(extra)
    if not scan:
        topics -= {N.robot_topic(r, N.SCAN) for r in robots}
    return sorted(topics)


def required_topics(robots: Sequence[str], *, scan: bool = True) -> list[str]:
    robot = [N.robot_topic(r, leaf) for r in robots for leaf in REQUIRED_ROBOT if scan or leaf != N.SCAN]
    return sorted(set(REQUIRED_TEAM) | set(robot))


def qos_overrides(topics: Iterable[str]) -> dict:
    """rosbag2 QoS overrides: latched topics are recorded transient-local, so the recorder still
    gets a message published before it subscribed (/tf_static, the mission, a FALLBACK plan).
    The publishers of these topics are transient-local too (tf2, mrs_common.qos), so they match."""
    latched = (set(LATCHED_TOPICS) | {TF_STATIC}) & set(topics)
    return {t: {"history": "keep_last", "depth": 10, "reliability": "reliable", "durability": "transient_local"}
            for t in sorted(latched)}


def record_command(bag_dir: str | Path, topics: Sequence[str], *, overrides_file: str | Path = "",
                   sim_time: bool = True, profile: str = "zstd_fast") -> list[str]:
    """ros2 bag record with MCAP storage. With sim_time the bag is stamped in simulation time
    (lines up with events.jsonl and the Gazebo log), but nothing is written before the first /clock."""
    cmd = ["ros2", "bag", "record", "-s", "mcap", "--storage-preset-profile", profile, "-o", str(bag_dir)]
    if sim_time:
        cmd.append("--use-sim-time")
    if overrides_file:
        cmd += ["--qos-profile-overrides-path", str(overrides_file)]
    return cmd + ["--topics", *topics]
