"""Module 7 telemetry node: writes events.jsonl, one line per decision on the /mrs topics.

  ros2 run mrs_telemetry telemetry --ros-args -p events_file:=results/dev/run1/events.jsonl -p use_sim_time:=true
  (normally started by record.launch.py)

It subscribes to the decision topics (events.EVENT_TOPICS) with the same QoS as their publishers
(mrs_common.qos), so it sees exactly what the planner, gate and executor see. `t` in every line is
the node clock: simulation time with use_sim_time, so it lines up with the bag and the Gazebo log.
It writes telemetry_start when it starts and telemetry_end (with counts) when it stops; check_run
uses telemetry_end to tell a clean stop from a killed one.
"""
from __future__ import annotations

import signal

import rclpy
from mrs_msgs.msg import (DialogueTurn, EpisodeEvent, EthicalVerdict, ProposedPlan, SafetyOverride, TaskStatus,
                          ValidatedPlan)
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException
from rclpy.logging import get_logger
from rclpy.node import Node
from std_msgs.msg import String

from mrs_common import names as N
from mrs_common.qos import qos_for

from .events import EVENT_TOPICS, EventLog

MSG_TYPES = {N.MISSION: String, N.DIALOGUE: DialogueTurn, N.PROPOSED_PLAN: ProposedPlan,
             N.VERDICT: EthicalVerdict, N.VALIDATED_PLAN: ValidatedPlan, N.TASK_STATUS: TaskStatus,
             N.EPISODE_EVENT: EpisodeEvent, N.SAFETY_OVERRIDE: SafetyOverride}


class TelemetryNode(Node):
    def __init__(self, **kwargs):
        super().__init__("mrs_telemetry", **kwargs)
        ro = lambda d: ParameterDescriptor(description=d, read_only=True)  # noqa: E731
        path = self.declare_parameter("events_file", "events.jsonl", ro("where to append the events")).value
        interval = self.declare_parameter("override_interval_s", 1.0,
                                          ro("at most one safety_override event per robot per this many "
                                             "seconds of sim time; the bag keeps all")).value
        if not path:
            raise ValueError("events_file must not be empty")
        self.log = EventLog(path, override_interval=float(interval))
        assert set(MSG_TYPES) == set(EVENT_TOPICS), "a decision topic without a message type"
        for topic in EVENT_TOPICS:
            self.create_subscription(MSG_TYPES[topic], topic, lambda msg, t=topic: self.on_msg(t, msg),
                                     qos_for(topic))
        self.log.note("telemetry_start", self.t(), f"recording decisions to {path}", topics=list(EVENT_TOPICS))
        self.get_logger().info(f"writing {path}")

    def t(self):
        ns = self.get_clock().now().nanoseconds
        return round(ns / 1e9, 6) if ns else None   # 0 = sim time before the first /clock

    def on_msg(self, topic: str, msg) -> None:
        try:
            self.log.record(topic, msg, t=self.t())
        except Exception as e:   # a strange message must not stop the recording
            self.get_logger().error(f"could not record a message on {topic}: {type(e).__name__}: {e}")

    def close(self) -> None:
        if not self.log._f.closed:
            counts = {k: v for k, v in self.log.counts.items() if not k.startswith("telemetry_")}
            self.log.note("telemetry_end", self.t(), f"{sum(counts.values())} events", counts=counts)
            self.log.close()


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = TelemetryNode()
    except ValueError as e:
        get_logger("mrs_telemetry").fatal(f"not starting: {e}")
        rclpy.try_shutdown()
        raise SystemExit(1)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)   # a second Ctrl-C must not cut telemetry_end short
        node.close()
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
