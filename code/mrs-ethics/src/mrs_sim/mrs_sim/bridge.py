"""ros_gz_bridge config for N robots (Module 2). Pure Python, no ROS imports.

Gazebo topics already use the canonical names (see templates/mrs_bot.sdf), so each entry maps
a topic to itself. The two exceptions: every robot's /<robot>/tf goes to the shared ROS /tf, and
/clock comes from the world.
"""
from __future__ import annotations

from typing import Iterable

import yaml

from mrs_common import names as N

GZ_TYPES = {
    "sensor_msgs/msg/LaserScan": "gz.msgs.LaserScan",
    "nav_msgs/msg/Odometry": "gz.msgs.Odometry",
    "geometry_msgs/msg/Twist": "gz.msgs.Twist",
    "sensor_msgs/msg/Image": "gz.msgs.Image",
    "sensor_msgs/msg/CameraInfo": "gz.msgs.CameraInfo",
    "tf2_msgs/msg/TFMessage": "gz.msgs.Pose_V",
    "rosgraph_msgs/msg/Clock": "gz.msgs.Clock",
}

# Robot topics the sim publishes (GZ_TO_ROS) or listens to (ROS_TO_GZ). world_state and
# cmd_vel_nominal are ROS-only (Modules 3 and 6), so they are not bridged.
_GZ_TO_ROS = (N.SCAN, N.ODOM, N.GROUND_TRUTH)
_ROS_TO_GZ = (N.CMD_VEL,)


def _entry(ros: str, gz: str, ros_type: str, direction: str) -> dict:
    e = {"ros_topic_name": ros, "gz_topic_name": gz, "ros_type_name": ros_type,
         "gz_type_name": GZ_TYPES[ros_type], "direction": direction}
    if direction == "ROS_TO_GZ":
        e["lazy"] = False  # subscribe immediately, so the first command is never dropped
    return e


def bridge_entries(robot_ids: Iterable[str], camera: bool = True) -> list[dict]:
    out = [_entry(N.CLOCK, N.CLOCK, "rosgraph_msgs/msg/Clock", "GZ_TO_ROS")]
    for r in robot_ids:
        for name in _GZ_TO_ROS:
            t = N.robot_topic(r, name)
            out.append(_entry(t, t, N.ROBOT_TOPIC_TYPES[name], "GZ_TO_ROS"))
        for name in _ROS_TO_GZ:
            t = N.robot_topic(r, name)
            out.append(_entry(t, t, N.ROBOT_TOPIC_TYPES[name], "ROS_TO_GZ"))
        out.append(_entry("/tf", f"/{r}/tf", "tf2_msgs/msg/TFMessage", "GZ_TO_ROS"))
        if camera:
            for name, typ in N.CAMERA_TOPIC_TYPES.items():
                t = N.robot_topic(r, name)
                out.append(_entry(t, t, typ, "GZ_TO_ROS"))
    return out


def bridge_yaml(robot_ids: Iterable[str], camera: bool = True) -> str:
    return yaml.safe_dump(bridge_entries(robot_ids, camera), sort_keys=False)
