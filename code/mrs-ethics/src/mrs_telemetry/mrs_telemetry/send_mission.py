"""Send a mission once, after everyone who must hear it has subscribed (Module 7, used by episode.launch.py).

  ros2 run mrs_telemetry send_mission --ros-args -p text:="robot_a: go to room_e. robot_b: go to room_w." \
      -p wait_for:=3 -p use_sim_time:=false

`ros2 topic pub --once` can publish before the coordinator or the recorder has connected, and the
mission is then lost for them. This node publishes with the mission's own QoS (reliable,
transient-local: mrs_common.qos), waits until `wait_for` subscribers have matched (or `timeout_s`
has passed, then it publishes anyway and warns), and stays up `keep_alive_s` afterwards so a late
subscriber still receives it.
"""
from __future__ import annotations

import time

import rclpy
from rclpy.logging import get_logger
from rclpy.node import Node
from std_msgs.msg import String

from mrs_common import names as N
from mrs_common.qos import qos_for


class MissionSender(Node):
    def __init__(self, **kwargs):
        super().__init__("mrs_send_mission", **kwargs)
        self.text = self.declare_parameter("text", "").value
        self.wait_for = int(self.declare_parameter("wait_for", 1).value)
        self.timeout_s = float(self.declare_parameter("timeout_s", 60.0).value)
        self.keep_alive_s = float(self.declare_parameter("keep_alive_s", 5.0).value)
        if not self.text.strip():
            raise ValueError("text (the mission) must not be empty")
        self.pub = self.create_publisher(String, N.MISSION, qos_for(N.MISSION))


def main(args=None) -> int:
    rclpy.init(args=args)
    try:
        node = MissionSender()
    except ValueError as e:
        get_logger("mrs_send_mission").fatal(str(e))
        rclpy.try_shutdown()
        return 1
    log = node.get_logger()
    try:
        start = time.monotonic()   # wall clock: works with or without /clock
        while node.pub.get_subscription_count() < node.wait_for and time.monotonic() - start < node.timeout_s:
            rclpy.spin_once(node, timeout_sec=0.2)
        n = node.pub.get_subscription_count()
        if n < node.wait_for:
            log.warn(f"only {n} of {node.wait_for} subscribers after {node.timeout_s:.0f} s; sending anyway")
        node.pub.publish(String(data=node.text))
        log.info(f"mission sent to {n} subscribers: {node.text}")
        end = time.monotonic() + node.keep_alive_s
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
