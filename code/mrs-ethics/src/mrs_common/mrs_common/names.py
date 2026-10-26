"""Canonical topic and frame names (Module 1). Pure Python, no ROS imports.

Convention: every robot-level topic lives under /<robot_id>/..., every team-level
topic under /mrs/... . Never hard-code a topic string in a node — import it from here,
so a rename is a one-line change and `check_interfaces` can list everything.
"""
from __future__ import annotations

import re
from typing import Iterable

_ROBOT_ID = re.compile(r"^[a-z][a-z0-9_]*$")

# ---- robot-level topics: /<robot_id>/<name> -------------------------------
SCAN = "scan"
ODOM = "odom"
GROUND_TRUTH = "ground_truth"
WORLD_STATE = "world_state"
CMD_VEL_NOMINAL = "cmd_vel_nominal"
CMD_VEL = "cmd_vel"

ROBOT_TOPIC_TYPES: dict[str, str] = {
    SCAN: "sensor_msgs/msg/LaserScan",
    ODOM: "nav_msgs/msg/Odometry",
    GROUND_TRUTH: "nav_msgs/msg/Odometry",
    WORLD_STATE: "mrs_msgs/msg/WorldState",
    CMD_VEL_NOMINAL: "geometry_msgs/msg/Twist",
    CMD_VEL: "geometry_msgs/msg/Twist",  # switch to TwistStamped if the robot's bridge expects it (Module 2)
}

# ---- team-level topics -----------------------------------------------------
TEAM_STATE = "/mrs/team_state"
MISSION = "/mrs/mission"
DIALOGUE = "/mrs/dialogue"
PROPOSED_PLAN = "/mrs/proposed_plan"
VERDICT = "/mrs/verdict"
VALIDATED_PLAN = "/mrs/validated_plan"
SAFETY_OVERRIDE = "/mrs/safety_override"
TASK_STATUS = "/mrs/task_status"
EPISODE_EVENT = "/mrs/episode_event"
MARKERS = "/mrs/markers"
CLOCK = "/clock"

TEAM_TOPIC_TYPES: dict[str, str] = {
    TEAM_STATE: "mrs_msgs/msg/TeamState",
    MISSION: "std_msgs/msg/String",
    DIALOGUE: "mrs_msgs/msg/DialogueTurn",
    PROPOSED_PLAN: "mrs_msgs/msg/ProposedPlan",
    VERDICT: "mrs_msgs/msg/EthicalVerdict",
    VALIDATED_PLAN: "mrs_msgs/msg/ValidatedPlan",
    SAFETY_OVERRIDE: "mrs_msgs/msg/SafetyOverride",
    TASK_STATUS: "mrs_msgs/msg/TaskStatus",
    EPISODE_EVENT: "mrs_msgs/msg/EpisodeEvent",
    MARKERS: "visualization_msgs/msg/MarkerArray",
}

# ---- frames ------------------------------------------------------------------
MAP_FRAME = "map"


def validate_robot_id(robot_id: str) -> str:
    if not _ROBOT_ID.match(robot_id or ""):
        raise ValueError(f"invalid robot id '{robot_id}': use lowercase letters, digits, '_' (e.g. robot_a)")
    return robot_id


def robot_topic(robot_id: str, name: str) -> str:
    """robot_topic('robot_a', SCAN) -> '/robot_a/scan'"""
    if name not in ROBOT_TOPIC_TYPES:
        raise ValueError(f"'{name}' is not a canonical robot topic: {sorted(ROBOT_TOPIC_TYPES)}")
    return f"/{validate_robot_id(robot_id)}/{name}"


def odom_frame(robot_id: str) -> str:
    return f"{validate_robot_id(robot_id)}/odom"


def base_frame(robot_id: str) -> str:
    return f"{validate_robot_id(robot_id)}/base_link"


def lidar_frame(robot_id: str) -> str:
    return f"{validate_robot_id(robot_id)}/lidar"


def robot_ids(n: int) -> list[str]:
    """Default ids: robot_a, robot_b, robot_c, ..."""
    if not 1 <= n <= 26:
        raise ValueError("between 1 and 26 robots")
    return [f"robot_{chr(ord('a') + i)}" for i in range(n)]


def all_topics(robots: Iterable[str]) -> dict[str, str]:
    """Every canonical topic -> type for a robot list. Module 7 records exactly this set (+ /tf)."""
    out = {robot_topic(r, n): t for r in robots for n, t in ROBOT_TOPIC_TYPES.items()}
    out.update(TEAM_TOPIC_TYPES)
    return out
