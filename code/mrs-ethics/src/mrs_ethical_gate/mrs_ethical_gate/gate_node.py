"""Module 5 gate node: one EthicalVerdict for every ProposedPlan, and the ValidatedPlan on APPROVE.

  ros2 launch mrs_ethical_gate gate.launch.py
  ros2 launch mrs_ethical_gate gate.launch.py map_file:=/path/to/map.yaml use_sim_time:=false
  ros2 launch mrs_ethical_gate gate.launch.py people_file:=/path/to/people.yaml   # S-2

The callback receives, calls GateSession.decide and publishes; what it decides is in
session.py and tested without ROS. A message seen before gets the same verdict (and
ValidatedPlan) again.

With start_source team_state the node also subscribes to /mrs/team_state and hands each
message to the session's TeamStateStarts, which keeps the latest; a plan is checked where
that one puts the robots (starts.py). The decision log line says how old it was.

latency_ms is the wall-clock time of decide(): reading, compiling and checking the plan and
building the verdict. It is measured with time.perf_counter, not the node clock, because with
use_sim_time the node clock is simulation time, which can pause or run at another rate. It
does not include receiving or publishing.

FALLBACK plans (hold_all_plan, source_verdict FALLBACK) do not pass through the gate: Module 4
publishes them on /mrs/validated_plan itself (an assumption to confirm, rules_v1.md).
"""
from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from mrs_msgs.msg import EthicalVerdict, ProposedPlan, TeamState, ValidatedPlan
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.exceptions import ParameterUninitializedException
from rclpy.executors import ExternalShutdownException
from rclpy.logging import get_logger
from rclpy.node import Node
from rclpy.parameter import Parameter

from mrs_common import names as N
from mrs_common.convert import make_validated_plan, make_verdict
from mrs_common.qos import qos_for

from .session import GateSession
from .settings import PARAMETERS, REQUIRED, SettingsError, load_settings
from .starts import TeamStateStarts, team_snapshot

_ROS_TYPES = {str: Parameter.Type.STRING, int: Parameter.Type.INTEGER, float: Parameter.Type.DOUBLE,
              bool: Parameter.Type.BOOL}


class GateNode(Node):
    def __init__(self, **kwargs):
        super().__init__("mrs_ethical_gate", **kwargs)
        settings = load_settings(self._declare_parameters(), default_policy_file=_config("policy.yaml"),
                                 default_people_file=_config("people.yaml"))
        self.session = GateSession.from_settings(settings)
        self.get_logger().info("settings " + json.dumps(settings.describe()))
        self.get_logger().info(settings.rules_line())
        self.verdict_pub = self.create_publisher(EthicalVerdict, N.VERDICT, qos_for(N.VERDICT))
        self.validated_pub = self.create_publisher(ValidatedPlan, N.VALIDATED_PLAN, qos_for(N.VALIDATED_PLAN))
        self.create_subscription(ProposedPlan, N.PROPOSED_PLAN, self.on_plan, qos_for(N.PROPOSED_PLAN))
        if isinstance(self.session.starts, TeamStateStarts):
            self.create_subscription(TeamState, N.TEAM_STATE, self.on_team_state, qos_for(N.TEAM_STATE))

    def _declare_parameters(self) -> dict:
        """Declare every parameter of settings.PARAMETERS, read-only; None for one never set."""
        values = {}
        for name, (kind, default, description) in PARAMETERS.items():
            descriptor = ParameterDescriptor(description=description, read_only=True)
            self.declare_parameter(name, _ROS_TYPES[kind] if default is REQUIRED else default, descriptor)
            try:
                values[name] = self.get_parameter(name).value
            except ParameterUninitializedException:
                values[name] = None
        return values

    def on_plan(self, msg: ProposedPlan) -> None:
        start = time.perf_counter()
        d = self.session.decide(msg.plan_json, transaction_id=msg.transaction_id,
                                episode_id=msg.episode_id, attempt=msg.attempt, now=self._now_s())
        latency_ms = (time.perf_counter() - start) * 1e3
        ids = {"transaction_id": msg.transaction_id, "episode_id": msg.episode_id, "attempt": msg.attempt}
        self.verdict_pub.publish(make_verdict(**ids, latency_ms=latency_ms, node=self,
                                              **d.verdict.make_verdict_kwargs()))
        if d.validated is not None:
            self.validated_pub.publish(make_validated_plan(d.validated, **ids, source_verdict="APPROVE",
                                                           node=self))
        self.get_logger().info("decision " + json.dumps({**ids, "latency_ms": round(latency_ms, 3),
                                                         **d.summary()}))

    def on_team_state(self, msg: TeamState) -> None:
        first = self.session.starts.latest is None
        self.session.starts.observe(team_snapshot(msg))
        if first:
            self.get_logger().info(f"first {N.TEAM_STATE}: episode {msg.episode_id!r}, robots "
                                   f"{[r.robot_id for r in msg.robots]}")

    def _now_s(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9


def _config(name: str) -> Path:
    """A file of this package's installed config/ directory."""
    return Path(get_package_share_directory("mrs_ethical_gate")) / "config" / name


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = GateNode()
    except SettingsError as e:
        get_logger("mrs_ethical_gate").fatal(f"not starting: {e}")
        rclpy.try_shutdown()
        raise SystemExit(1)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # Under ros2 launch, Ctrl-C reaches the node twice: from the terminal and then from
        # launch. Without this, the second SIGINT interrupts destroy_node() and launch reports
        # the node as died (exit code -2) with a traceback.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
