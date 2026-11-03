"""Development node for testing the gate: a scripted stand-in for the planner, with no LLM.

Not Module 4: it reads no mission and no world state, and makes no plan of its own. It sends
a scenario's TeamPlan, applies each REJECT's constraints to it and sends it again, and when
it cannot go on, falls back to hold_all_plan the way Module 4 is assumed to (rules_v1.md,
"Module 5 gate"). model_name "fake" marks every plan it sends.

  ros2 launch mrs_ethical_gate replan_loop.launch.py scenario:=s1_passage_no_order \
      map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml      # the gate and this node
  ros2 run mrs_ethical_gate fake_planner --ros-args -p scenario:=s1_passage_no_order \
      -p map_file:=$PWD/src/fleet_checker/test/fixtures/samples_world.yaml   # to a gate already running

  out  /mrs/proposed_plan    each attempt; dialogue_digest says what changed since the last one
  in   /mrs/verdict          the verdict for the attempt it waits for; any other is ignored
  out  /mrs/validated_plan   only when stuck or exhausted: hold_all_plan, source_verdict FALLBACK
  out  /mrs/episode_event    with it, a planning_exhausted event

It starts once the gate is there (proposed_plan and verdict are volatile: a plan sent before
the gate has connected is lost). What it decides is in replan_loop.py, scripted_repair.py,
scenarios.py and run_settings.py, which import no ROS. It logs one JSON line per round and
the summary, and exits when the loop is over: 0 when the loop ran to an outcome, whatever it
was (see the summary), 1 for a parameter or scenario it cannot run, 2 when no gate came up or
a verdict did not come. Its timeouts run on a steady clock, not the node clock: under
use_sim_time the node clock stands still until /clock is published.
"""
from __future__ import annotations

import json
import signal
import time
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from mrs_msgs.msg import EpisodeEvent, EthicalVerdict, ProposedPlan, ValidatedPlan
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.clock import Clock, ClockType
from rclpy.executors import ExternalShutdownException
from rclpy.logging import get_logger
from rclpy.node import Node

from mrs_common import names as N
from mrs_common.convert import make_episode_event, make_proposed_plan, make_validated_plan
from mrs_common.ids import new_transaction_id
from mrs_common.qos import qos_for

from .replan_loop import FakePlanner, Finished, GateVerdict, Proposal
from .run_settings import PARAMETERS, load_run
from .scenarios import ScenarioError

MODEL_NAME = "fake"
EXIT_DONE, EXIT_CANNOT_RUN, EXIT_NO_GATE = 0, 1, 2
LINGER_S = 1.0   # after the FALLBACK: time to deliver it before the process, and its publishers, go
_TICK_S = 0.05


class FakePlannerNode(Node):
    def __init__(self, **kwargs):
        super().__init__("fake_planner", **kwargs)
        self.run = load_run(self._declare_parameters(), scenarios_dir=_scenarios_dir())
        run = self.run
        self.loop = FakePlanner(run.scenario.plan, graph=run.sim_map.neighbors, start_nodes=run.start_nodes,
                                max_attempts=run.max_attempts, transaction_id=new_transaction_id(),
                                episode_id=run.episode_id,
                                context={"scenario": run.scenario.name, "description": run.scenario.description,
                                         "map": run.sim_map.name})
        self.exit_code: int | None = None   # set when the node is done
        self.proposed_pub = self.create_publisher(ProposedPlan, N.PROPOSED_PLAN, qos_for(N.PROPOSED_PLAN))
        self.validated_pub = self.create_publisher(ValidatedPlan, N.VALIDATED_PLAN, qos_for(N.VALIDATED_PLAN))
        self.event_pub = self.create_publisher(EpisodeEvent, N.EPISODE_EVENT, qos_for(N.EPISODE_EVENT))
        self.create_subscription(EthicalVerdict, N.VERDICT, self.on_verdict, qos_for(N.VERDICT))
        self._started = False
        self._deadline = time.monotonic() + run.gate_wait
        self._exit_at: float | None = None
        self._exit_with = EXIT_DONE
        self.create_timer(_TICK_S, self._tick, clock=Clock(clock_type=ClockType.STEADY_TIME))
        self.get_logger().info("settings " + json.dumps(run.describe()))

    def _declare_parameters(self) -> dict:
        """Declare every parameter of run_settings.PARAMETERS, read-only, and read them."""
        values = {}
        for name, (_, default, description) in PARAMETERS.items():
            self.declare_parameter(name, default, ParameterDescriptor(description=description, read_only=True))
            values[name] = self.get_parameter(name).value
        return values

    def on_verdict(self, msg: EthicalVerdict) -> None:
        step = self.loop.on_verdict(GateVerdict(
            transaction_id=msg.transaction_id, attempt=msg.attempt, verdict=msg.verdict,
            rule_ids=tuple(msg.rule_ids), rationale=msg.rationale, constraints_json=msg.constraints_json,
            risk_score=round(float(msg.risk_score), 6),   # float32 on the wire
            latency_ms=round(msg.latency_ms, 3)))         # as the gate logs it
        if step is None:
            return
        self.get_logger().info("round " + json.dumps(self.loop.rounds[-1]))
        if isinstance(step, Proposal):
            self._send(step)
        else:
            self._finish(step, EXIT_DONE)

    def _tick(self) -> None:
        now = time.monotonic()
        if self._exit_at is not None:
            if now >= self._exit_at:
                self.exit_code = self._exit_with
            return
        if not self._started:
            if self.proposed_pub.get_subscription_count() > 0 and self.count_publishers(N.VERDICT) > 0:
                self._send(self.loop.first())
            elif now > self._deadline:
                self.get_logger().error(f"no gate after {self.run.gate_wait:g} s: nothing subscribes to "
                                        f"{N.PROPOSED_PLAN} or publishes {N.VERDICT}. Is the gate running?")
                self._exit(EXIT_NO_GATE, linger=0.0)
        elif self.loop.finished is None and now > self._deadline:
            self.get_logger().error(f"no verdict for attempt {self.loop.awaiting} within "
                                    f"{self.run.verdict_timeout:g} s")
            self._finish(self.loop.timed_out(), EXIT_NO_GATE)

    def _send(self, proposal: Proposal) -> None:
        self._started = True
        self.proposed_pub.publish(make_proposed_plan(
            proposal.plan, episode_id=self.loop.episode_id, attempt=proposal.attempt,
            transaction_id=self.loop.transaction_id, model_name=MODEL_NAME, latency_ms=proposal.planning_ms,
            dialogue_digest=proposal.digest, node=self))
        self._deadline = time.monotonic() + self.run.verdict_timeout
        self.get_logger().info(f"sent attempt {proposal.attempt} of transaction {self.loop.transaction_id}: "
                               f"{proposal.digest}")

    def _finish(self, finished: Finished, exit_code: int) -> None:
        linger = 0.0
        if finished.fallback is not None:
            self.validated_pub.publish(make_validated_plan(
                finished.fallback, transaction_id=self.loop.transaction_id, episode_id=self.loop.episode_id,
                attempt=finished.fallback_attempt, source_verdict="FALLBACK", node=self))
            self.event_pub.publish(make_episode_event(
                episode_id=self.loop.episode_id, kind=EpisodeEvent.KIND_PLANNING_EXHAUSTED,
                detail=finished.event_detail, node=self))
            self.get_logger().warning(f"{finished.outcome}: sent the FALLBACK plan on {N.VALIDATED_PLAN} "
                                      f"and a planning_exhausted event on {N.EPISODE_EVENT}")
            linger = LINGER_S
        for line in self.loop.summary_text():
            self.get_logger().info(line)
        summary = self.loop.summary()
        self.get_logger().info("summary " + json.dumps({k: v for k, v in summary.items() if k != "round_log"}))
        if self.run.summary_file is not None:
            self.run.summary_file.write_text(json.dumps(summary, indent=2) + "\n")
            self.get_logger().info(f"summary written to {self.run.summary_file}")
        self._exit(exit_code, linger)

    def _exit(self, code: int, linger: float) -> None:
        self._exit_with, self._exit_at = code, time.monotonic() + linger


def _scenarios_dir() -> Path:
    return Path(get_package_share_directory("mrs_ethical_gate")) / "scenarios"


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = FakePlannerNode()
    except ScenarioError as e:
        get_logger("fake_planner").fatal(f"not starting: {e}")
        rclpy.try_shutdown()
        raise SystemExit(EXIT_CANNOT_RUN)
    code = EXIT_DONE
    try:
        while rclpy.ok() and node.exit_code is None:
            rclpy.spin_once(node, timeout_sec=_TICK_S)
        if node.exit_code is not None:
            code = node.exit_code
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
