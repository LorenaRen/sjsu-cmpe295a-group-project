"""Send a mission once, after everyone who must hear it has subscribed (Module 7, used by episode.launch.py).

  ros2 run mrs_telemetry send_mission --ros-args -p text:="robot_a: go to room_e. robot_b: go to room_w." \
      -p wait_for:=3 -p use_sim_time:=false

`ros2 topic pub --once` can publish before the coordinator or the recorder has connected, and the
mission is then lost for them. This node publishes with the mission's own QoS (reliable,
transient-local: mrs_common.qos), waits until `wait_for` subscribers have matched (or `timeout_s`
has passed, then it publishes anyway and warns), and stays up `keep_alive_s` afterwards so a late
subscriber still receives it.

With wait_for_plan:=true it then waits for the planning cycle to end (episode_end.EndWatcher): the first
ValidatedPlan after the mission, APPROVE or FALLBACK, plus `grace_s`. Then it exits with code 0, which
episode.launch.py turns into the end of the episode. `plan_timeout_s` (0 = none) gives up waiting.
"""
from __future__ import annotations

import time

import rclpy
from mrs_msgs.msg import ValidatedPlan
from rclpy.logging import get_logger
from rclpy.node import Node
from std_msgs.msg import String

from mrs_common import names as N
from mrs_common.qos import qos_for

from .episode_end import EndWatcher


class MissionSender(Node):
    def __init__(self, **kwargs):
        super().__init__("mrs_send_mission", **kwargs)
        self.text = self.declare_parameter("text", "").value
        self.wait_for = int(self.declare_parameter("wait_for", 1).value)
        self.timeout_s = float(self.declare_parameter("timeout_s", 60.0).value)
        self.keep_alive_s = float(self.declare_parameter("keep_alive_s", 5.0).value)
        self.wait_for_plan = bool(self.declare_parameter("wait_for_plan", False).value)
        self.plan_timeout_s = float(self.declare_parameter("plan_timeout_s", 0.0).value)
        if not self.text.strip():
            raise ValueError("text (the mission) must not be empty")
        self.watch = EndWatcher(float(self.declare_parameter("grace_s", 5.0).value))
        self.pub = self.create_publisher(String, N.MISSION, qos_for(N.MISSION))
        if self.wait_for_plan:
            # subscribed before the mission is sent; plans that arrive before it do not count (EndWatcher)
            self.create_subscription(ValidatedPlan, N.VALIDATED_PLAN, self.on_plan, qos_for(N.VALIDATED_PLAN))

    def on_plan(self, msg: ValidatedPlan) -> None:
        if self.watch.on_plan(time.monotonic(), msg.source_verdict):
            self.get_logger().info(f"the robots got a plan ({msg.source_verdict}): the episode ends in "
                                   f"{self.watch.grace_s:g} s")


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
        sent = time.monotonic()
        node.watch.arm(sent)
        log.info(f"mission sent to {n} subscribers: {node.text}")
        while True:
            rclpy.spin_once(node, timeout_sec=0.2)
            now = time.monotonic()
            if now - sent < node.keep_alive_s:
                continue
            if not node.wait_for_plan or node.watch.due(now):
                break
            if node.plan_timeout_s > 0 and now - sent > node.plan_timeout_s:
                log.warn(f"no plan reached the robots within {node.plan_timeout_s:g} s of the mission")
                break
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
