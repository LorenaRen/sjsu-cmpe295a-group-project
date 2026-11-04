"""Development node for testing the gate: a stand-in for Module 3's /mrs/team_state, with no simulation.

Not Module 3: it measures and computes nothing. It publishes the robots of one snapshot file
(snapshots.py), the same positions every time, at `rate`, stamped with the node clock, for
the gate with start_source team_state (starts.py) to read.

  ros2 run mrs_ethical_gate fake_team_state --ros-args -p snapshot:=b_on_edge \
      -p map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml
  ros2 launch mrs_ethical_gate replan_loop.launch.py scenario:=s1_passage_no_order team_state:=at_spawns \
      map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml   # with the gate and the fake planner

  out  /mrs/team_state   a TeamState: one WorldState per robot of the snapshot (robot_id, at, x, y,
                         yaw; no zones, sectors or obstacles), every stamp the node clock now

With stop_after > 0 it stops publishing after that many seconds and exits, so that a test
can see the gate take the last snapshot for too old. Exit codes: 0 when it stopped, 1 for a
parameter or snapshot it cannot use. The period runs on a steady clock, as the fake planner's
timeouts do; the stamps come from the node clock, the clock the gate compares them with.
"""
from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from mrs_msgs.msg import TeamState, WorldState
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.clock import Clock, ClockType
from rclpy.executors import ExternalShutdownException
from rclpy.logging import get_logger
from rclpy.node import Node

from mrs_common import names as N
from mrs_common.qos import qos_for

from .scenarios import ScenarioError
from .snapshots import PARAMETERS, load_feed

EXIT_DONE, EXIT_CANNOT_RUN = 0, 1
_TICK_S = 0.05


class FakeTeamStateNode(Node):
    def __init__(self, **kwargs):
        super().__init__("fake_team_state", **kwargs)
        self.feed = load_feed(self._declare_parameters(), snapshots_dir=_snapshots_dir())
        self.pub = self.create_publisher(TeamState, N.TEAM_STATE, qos_for(N.TEAM_STATE))
        self.sent = 0
        self.exit_code: int | None = None   # set when the node is done
        self._stop_at = time.monotonic() + self.feed.stop_after if self.feed.stop_after > 0 else None
        self.get_logger().info("settings " + json.dumps(self.feed.describe()))
        self._publish()
        self.create_timer(1.0 / self.feed.rate, self._publish, clock=Clock(clock_type=ClockType.STEADY_TIME))

    def _declare_parameters(self) -> dict:
        """Declare every parameter of snapshots.PARAMETERS, read-only, and read them."""
        values = {}
        for name, (_, default, description) in PARAMETERS.items():
            self.declare_parameter(name, default, ParameterDescriptor(description=description, read_only=True))
            values[name] = self.get_parameter(name).value
        return values

    def _publish(self) -> None:
        if self.exit_code is not None:
            return
        if self._stop_at is not None and time.monotonic() >= self._stop_at:
            self.get_logger().info(f"stop_after {self.feed.stop_after:g} s: stopped after {self.sent} messages")
            self.exit_code = EXIT_DONE
            return
        self.pub.publish(self.message())
        self.sent += 1

    def message(self) -> TeamState:
        f, now = self.feed, self.get_clock().now().to_msg()
        msg = TeamState(episode_id=f.episode_id, blocked_edges=list(f.snapshot.blocked_edges), stamp=now)
        msg.robots = [WorldState(robot_id=r.robot_id, episode_id=f.episode_id, x=r.x, y=r.y, yaw=r.yaw, at=r.at,
                                 stamp=now)
                      for r in f.snapshot.robots]
        return msg


def _snapshots_dir() -> Path:
    return Path(get_package_share_directory("mrs_ethical_gate")) / "snapshots"


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = FakeTeamStateNode()
    except ScenarioError as e:
        get_logger("fake_team_state").fatal(f"not starting: {e}")
        rclpy.try_shutdown()
        raise SystemExit(EXIT_CANNOT_RUN)
    code = EXIT_DONE
    try:
        while rclpy.ok() and node.exit_code is None:
            rclpy.spin_once(node, timeout_sec=_TICK_S)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        # As in the gate: under ros2 launch, a second SIGINT would interrupt destroy_node().
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.destroy_node()
        rclpy.try_shutdown()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
