"""Module 4 coordinator node: runs the agents' dialogue and hands the agreed plan to the gate.

  ros2 launch mrs_llm_planner planner.launch.py
  ros2 topic pub --once /mrs/mission std_msgs/msg/String "{data: 'robot_a: go to room_e. robot_b: go to room_w.'}" \
      --qos-durability transient_local --qos-reliability reliable

Every decision is in protocol.Coordinator (pure Python, tested without ROS); this node feeds it
messages and carries out the actions it returns:

  /mrs/mission (String, latched)  a new text starts a planning cycle (an identical repeat does not)
  /mrs/dialogue                   agents' turns in; the coordinator's prompts out
  /mrs/proposed_plan              the agreed plan out, to the gate
  /mrs/verdict                    the gate's verdict in
  /mrs/validated_plan             FALLBACK (all hold) out, when no plan is approved
  /mrs/episode_event              planning_exhausted out
  /mrs/team_state                 where the robots are: the opening prompt and the fallback plan

Deadlines use the wall clock (time.monotonic), not the node clock: with use_sim_time the node
clock is simulation time, which stops while the world is paused, and LLM calls do not.
"""
from __future__ import annotations

import json
import signal
import time

import rclpy
from mrs_msgs.msg import DialogueTurn, EpisodeEvent, EthicalVerdict, ProposedPlan, TeamState, ValidatedPlan
from rclpy.executors import ExternalShutdownException
from rclpy.logging import get_logger
from rclpy.node import Node
from std_msgs.msg import String

from mrs_common import names as N
from mrs_common.convert import (make_episode_event, make_proposed_plan,
                                make_turn_prompt, make_validated_plan, plan_from_msg)
from mrs_common.ids import new_episode_id
from mrs_common.qos import qos_for

from .agent_node import declare_parameters
from .mission import MissionError, parse_mission, room_nodes
from .protocol import (CycleDone, Coordinator, Fallback, PauseWorld, ProtocolConfig,
                       ResumeWorld, SendPrompt, StartReplan, SubmitPlan, Turn, Verdict, fallback_plan)
from .response_log import ResponseLog
from .settings import SettingsError, load_map, load_settings


class CoordinatorNode(Node):
    def __init__(self, **kwargs):
        super().__init__("mrs_coordinator", **kwargs)
        s = load_settings(declare_parameters(self), agent=False)
        self.settings = s
        world_map, self.spawn_nodes = load_map(s)
        self.world_name = world_map.world
        self.graph_nodes = tuple(world_map.nodes)
        self.rooms = room_nodes(world_map)
        self.episode_id = s.episode_id or new_episode_id(self.world_name)
        self.coord = Coordinator(ProtocolConfig(team=s.team, max_rounds=s.max_rounds,
                                                turn_timeout_s=s.turn_timeout_s,
                                                verdict_timeout_s=s.verdict_timeout_s, pause_world=s.pause_world,
                                                cycle_timeout_s=s.cycle_timeout_s,
                                                max_replan_attempts=s.max_replan_attempts,
                                                replan_timeout_s=s.replan_timeout_s),
                                 start_nodes=self.spawn_nodes)
        self.log = ResponseLog(s.log_file, s.run_id)   # one row per planning cycle (summary.py reads it)
        self.mission = ""
        self._world_control = None

        self.dialogue_pub = self.create_publisher(DialogueTurn, N.DIALOGUE, qos_for(N.DIALOGUE))
        self.plan_pub = self.create_publisher(ProposedPlan, N.PROPOSED_PLAN, qos_for(N.PROPOSED_PLAN))
        self.validated_pub = self.create_publisher(ValidatedPlan, N.VALIDATED_PLAN, qos_for(N.VALIDATED_PLAN))
        self.event_pub = self.create_publisher(EpisodeEvent, N.EPISODE_EVENT, qos_for(N.EPISODE_EVENT))
        self.create_subscription(String, N.MISSION, self.on_mission, qos_for(N.MISSION))
        self.create_subscription(DialogueTurn, N.DIALOGUE, self.on_turn, qos_for(N.DIALOGUE))
        self.create_subscription(EthicalVerdict, N.VERDICT, self.on_verdict, qos_for(N.VERDICT))
        self.create_subscription(TeamState, N.TEAM_STATE, self.on_team_state, qos_for(N.TEAM_STATE))
        self.create_timer(0.5, self.on_tick)   # node clock; the deadlines themselves are wall clock
        self.get_logger().info("settings " + json.dumps({**s.describe(), "episode_id": self.episode_id}))

    # ---- inputs -------------------------------------------------------------------------------
    def on_mission(self, msg: String) -> None:
        text = msg.data.strip()
        if not text or text == self.mission:
            return
        self.mission = text
        try:
            mission = parse_mission(text, self.settings.team, self.graph_nodes, self.rooms)
        except MissionError as e:
            self.get_logger().error(f"mission refused, not planning: {e}")
            return
        self.get_logger().info(f"mission: {mission.text} {mission.goals_line()}")
        self._run(self.coord.start_cycle(mission, self.episode_id, time.monotonic()))

    def on_turn(self, msg: DialogueTurn) -> None:
        if msg.kind == DialogueTurn.KIND_PROMPT:
            return
        plan = None
        if msg.kind == DialogueTurn.KIND_PROPOSAL:
            try:
                plan = plan_from_msg(msg)
            except ValueError as e:   # the agent validated it; a bad one here is a bug or a stranger
                self.get_logger().error(f"proposal from {msg.speaker_id} has an unreadable plan: {e}")
        self._run(self.coord.on_turn(Turn(msg.transaction_id, msg.round, msg.kind, msg.speaker_id,
                                          msg.text, plan), time.monotonic()))

    def on_verdict(self, msg: EthicalVerdict) -> None:
        self._run(self.coord.on_verdict(Verdict(
            transaction_id=msg.transaction_id,
            verdict=msg.verdict,
            rule_ids=tuple(msg.rule_ids),
            rationale=msg.rationale,
            constraints_json=msg.constraints_json,
            attempt=msg.attempt,
        ), time.monotonic()))

    def on_team_state(self, msg: TeamState) -> None:
        self.coord.positions = {ws.robot_id: ws.at for ws in msg.robots}

    def on_tick(self) -> None:
        self._run(self.coord.tick(time.monotonic()))

    # ---- outputs ------------------------------------------------------------------------------
    def _run(self, actions) -> None:
        for a in actions:
            if isinstance(a, SendPrompt):
                self.dialogue_pub.publish(make_turn_prompt(episode_id=self.episode_id, transaction_id=a.transaction_id,
                                                           attempt=self.coord.attempt, round=a.round,
                                                           next_speaker=a.next_speaker, text=a.text, node=self))
            elif isinstance(a, SubmitPlan):
                self.plan_pub.publish(make_proposed_plan(
                    a.plan, episode_id=self.episode_id, attempt=a.attempt, transaction_id=a.transaction_id,
                    model_name=self.settings.llm.model_name, latency_ms=a.latency_ms,
                    dialogue_digest=a.dialogue_digest, node=self))
                self._log("submitted", transaction_id=a.transaction_id, attempt=a.attempt,
                          latency_ms=round(a.latency_ms, 1), digest=a.dialogue_digest)
            elif isinstance(a, StartReplan):
                self._do_replan(a)
            elif isinstance(a, Fallback):
                plan = fallback_plan(self.settings.team, self.coord.positions, self.spawn_nodes)
                self.validated_pub.publish(make_validated_plan(plan, transaction_id=a.transaction_id,
                                                               episode_id=self.episode_id, attempt=self.coord.attempt,
                                                               source_verdict="FALLBACK", node=self))
                self.event_pub.publish(make_episode_event(
                    episode_id=self.episode_id, kind=EpisodeEvent.KIND_PLANNING_EXHAUSTED,
                    detail={"transaction_id": a.transaction_id, "cause": a.cause, "detail": a.detail}, node=self))
                self._log("fallback", transaction_id=a.transaction_id, cause=a.cause, detail=a.detail)
            elif isinstance(a, CycleDone):
                stats = self.coord.stats()
                self._log("cycle_done", transaction_id=a.transaction_id, **stats)
                try:
                    self.log.record_cycle(episode_id=self.episode_id, transaction_id=a.transaction_id,
                                          mission=self.coord.mission, model=self.settings.llm.model_name,
                                          stats=stats, duration_s=time.monotonic() - self.coord.started)
                except Exception as e:   # the record is for analysis; planning must not stop for it
                    self.get_logger().error(f"could not record the cycle: {e}")
            elif isinstance(a, (PauseWorld, ResumeWorld)):
                self._control_world(pause=isinstance(a, PauseWorld))

    def _log(self, what: str, **fields) -> None:
        self.get_logger().info(f"{what} " + json.dumps(fields))

    def _do_replan(self, action: StartReplan) -> None:
        """Call the LLM replanner and feed the result back to the coordinator."""
        from mrs_common.plan_schema import constraints_from_json
        from mrs_ethical_gate.llm_replanner import LLMReplanner
        from mrs_ethical_gate.llm_replanner.ollama_client import OllamaClient

        self._log("replan_start", transaction_id=action.transaction_id, attempt=action.attempt)

        # Create replanner with same LLM settings
        client = OllamaClient(
            model=self.settings.llm.model_name,
            host=self.settings.llm.base_url or "http://localhost:11434",
            timeout=self.settings.llm.timeout_s,
        )
        replanner = LLMReplanner(
            max_attempts=1,  # We handle attempt counting in protocol.py
            start_nodes=self.spawn_nodes,
            transaction_id=action.transaction_id,
            episode_id=self.episode_id,
            client=client,
        )

        # Build constraints and call replanner
        constraints = constraints_from_json(action.constraints_json)
        result = replanner._replan_with_llm(action.plan, constraints, action.rationale, action.attempt - 1)

        if result.success:
            self._log("replan_success", transaction_id=action.transaction_id, attempt=action.attempt,
                      tokens_in=result.tokens_in, tokens_out=result.tokens_out,
                      latency_ms=round(result.latency_ms, 1))
            self._run(self.coord.on_replan_result(
                success=True,
                plan=result.plan,
                error_reason="",
                error_detail="",
                now=time.monotonic(),
            ))
        else:
            self._log("replan_failed", transaction_id=action.transaction_id, attempt=action.attempt,
                      error_reason=result.error_reason, error_detail=result.error_detail)
            self._run(self.coord.on_replan_result(
                success=False,
                plan=None,
                error_reason=result.error_reason or "replan_error",
                error_detail=result.error_detail or "unknown error",
                now=time.monotonic(),
            ))

    def _control_world(self, pause: bool) -> None:
        """Pause or resume Gazebo through ros_gz's ControlWorld service (needs a bridge entry for it)."""
        try:
            from ros_gz_interfaces.srv import ControlWorld
        except ImportError:
            self.get_logger().warn("pause_world: ros_gz_interfaces is not installed; the world keeps running")
            return
        if self._world_control is None:
            name = self.settings.world_control_service or f"/world/{self.world_name}/control"
            self._world_control = self.create_client(ControlWorld, name)
        if not self._world_control.service_is_ready():
            self.get_logger().warn(f"pause_world: service {self._world_control.srv_name} is not available")
            return
        req = ControlWorld.Request()
        req.world_control.pause = pause
        self._world_control.call_async(req)


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = CoordinatorNode()
    except SettingsError as e:
        get_logger("mrs_coordinator").fatal(f"not starting: {e}")
        rclpy.try_shutdown()
        raise SystemExit(1)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)   # see mrs_ethical_gate.gate_node.main
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
