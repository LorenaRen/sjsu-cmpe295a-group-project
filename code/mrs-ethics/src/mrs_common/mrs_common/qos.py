"""QoS conventions (Module 1). Publishers and subscribers MUST both use qos_for(topic):
mismatched QoS is the most common "my subscriber never receives anything" bug in ROS 2
(e.g. a TRANSIENT_LOCAL subscriber cannot connect to a VOLATILE publisher).

  sensors (scan, odom, ground_truth)   best effort, small queue       (what ros_gz_bridge publishes)
  commands (cmd_vel*)                  reliable, depth 1              (only the latest command matters)
  latched (mission, validated_plan)    reliable + transient local     (late joiners get the last one)
  decisions (everything else in /mrs)  reliable, deep queue           (never drop a plan or verdict)
"""
from __future__ import annotations

from . import names as N

LATCHED_TOPICS = {N.MISSION, N.VALIDATED_PLAN}


def _profiles():
    from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy,
                           qos_profile_sensor_data)

    return {
        "sensor": qos_profile_sensor_data,
        "command": QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE),
        "latched": QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE,
                              durability=DurabilityPolicy.TRANSIENT_LOCAL),
        "decision": QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=100, reliability=ReliabilityPolicy.RELIABLE,
                               durability=DurabilityPolicy.VOLATILE),
    }


def qos_class(topic: str) -> str:
    """Which QoS class a canonical topic belongs to (pure Python, testable without ROS)."""
    leaf = topic.rstrip("/").rsplit("/", 1)[-1]
    if topic in LATCHED_TOPICS:
        return "latched"
    if leaf in (N.SCAN, N.ODOM, N.GROUND_TRUTH):
        return "sensor"
    if leaf in (N.CMD_VEL, N.CMD_VEL_NOMINAL):
        return "command"
    return "decision"


def qos_for(topic: str):
    """rclpy QoSProfile for a canonical topic."""
    return _profiles()[qos_class(topic)]
