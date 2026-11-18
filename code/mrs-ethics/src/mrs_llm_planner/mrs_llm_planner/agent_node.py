"""Module 4 agent node: one per robot. Answers the coordinator's prompts that name its robot.

  ros2 run mrs_llm_planner agent --ros-args -p robot_id:=robot_a --params-file config/planner.yaml
  (normally started by planner.launch.py, one per robot)

It hears every DialogueTurn on /mrs/dialogue (TranscriptTracker keeps the current mission's) and
its robot's WorldState. When a prompt names its robot, it takes the turn in a worker thread (an
LLM call can take seconds; the node keeps receiving meanwhile) and publishes one DialogueTurn.
It is never silent: when its turn fails for any reason (no valid reply after the repairs, an LLM
timeout or error, even a bug in this node), it publishes a discussion turn saying so, which skips
its turn, and the dialogue goes on. Only a crashed process says nothing; the coordinator's turn
deadline catches that.

Every LLM request and response goes to the SQLite response log (log_file), with the episode,
transaction and round. Token counts are there, not in DialogueTurn.
"""
from __future__ import annotations

import json
import signal
import threading

import rclpy
from mrs_msgs.msg import DialogueTurn, WorldState
from rcl_interfaces.msg import ParameterDescriptor
from rclpy.executors import ExternalShutdownException
from rclpy.logging import get_logger
from rclpy.node import Node

from mrs_common import names as N
from mrs_common.convert import make_agreement, make_discussion, make_proposal, world_state_to_summary
from mrs_common.qos import qos_for

from .agent import Agent, TranscriptTracker, failure_text
from .guidelines import guidelines_for
from .llm_client import load_secrets, make_client
from .prompts import PlannerContext
from .routes import node_zones
from .response_log import CallContext, LoggingClient, ResponseLog
from .settings import PARAMETERS, SettingsError, load_map, load_settings


def declare_parameters(node: Node) -> dict:
    """Declare every parameter of settings.PARAMETERS, read-only; returns their values."""
    values = {}
    for name, (_, default, description) in PARAMETERS.items():
        node.declare_parameter(name, default, ParameterDescriptor(description=description, read_only=True))
        values[name] = node.get_parameter(name).value
    return values


class AgentNode(Node):
    def __init__(self, **kwargs):
        super().__init__("mrs_agent", **kwargs)
        s = load_settings(declare_parameters(self), agent=True)
        self.settings = s
        load_secrets(s.secrets_file or None)
        world_map, spawns = load_map(s)
        context = PlannerContext(s.robot_id, s.team, world_map.to_prompt_dict(), tuple(guidelines_for(s.rules)),
                                 max_rounds=s.max_rounds, node_zones=node_zones(world_map), start_nodes=spawns,
                                 conflict_hints=s.conflict_hints)
        self.client = LoggingClient(make_client(s.llm, fake_script=s.fake_script),
                                    ResponseLog(s.log_file, s.run_id), agent=s.robot_id)
        self.agent = Agent(context, self.client, temperature=s.temperature, constrained=s.constrained_output,
                           max_repairs=s.max_repairs, repair_temperature=s.repair_temperature)
        self.tracker = TranscriptTracker()
        self._warned_schema = False
        self.world_summary = None
        self._busy = threading.Lock()
        self._lock = threading.Lock()   # tracker and world_summary: callbacks vs. the worker thread

        self.pub = self.create_publisher(DialogueTurn, N.DIALOGUE, qos_for(N.DIALOGUE))
        self.create_subscription(DialogueTurn, N.DIALOGUE, self.on_turn, qos_for(N.DIALOGUE))
        topic = N.robot_topic(s.robot_id, N.WORLD_STATE)
        self.create_subscription(WorldState, topic, self.on_world_state, qos_for(topic))
        self.get_logger().info("settings " + json.dumps(s.describe()))

    def on_world_state(self, msg: WorldState) -> None:
        with self._lock:
            self.world_summary = world_state_to_summary(msg)

    def on_turn(self, msg: DialogueTurn) -> None:
        with self._lock:
            self.tracker.hear(transaction_id=msg.transaction_id, round=msg.round,
                              kind=msg.kind, speaker_id=msg.speaker_id, text=msg.text, plan_json=msg.plan_json)
        if msg.kind != DialogueTurn.KIND_PROMPT or msg.next_speaker != self.settings.robot_id:
            return
        key = (msg.transaction_id, msg.round)
        with self._lock:
            if key in self.tracker.answered:
                return   # the same prompt again
            if not self._busy.acquire(blocking=False):
                self.get_logger().warn(f"prompt {key} while still answering the previous one: skipped")
                return
            self.tracker.answered.add(key)
        threading.Thread(target=self._take_turn, args=(msg,), daemon=True).start()

    def _take_turn(self, prompt: DialogueTurn) -> None:
        ids = dict(episode_id=prompt.episode_id, transaction_id=prompt.transaction_id, attempt=prompt.attempt,
                   round=prompt.round, speaker_id=self.settings.robot_id, node=self)
        try:
            with self._lock:
                request = self.tracker.request(round=prompt.round, world_summary=self.world_summary)
            self.client.context = CallContext(prompt.episode_id, prompt.transaction_id, prompt.attempt, prompt.round)
            result = self.agent.take_turn(request)
            if self.agent.schema_off_reason and not self._warned_schema:
                self._warned_schema = True
                self.get_logger().warn("the LLM backend refused the reply schema; constrained output is off from "
                                       f"now on (plain JSON mode): {self.agent.schema_off_reason[:300]}")
            info = {"round": prompt.round, "calls": result.calls,
                    "latency_ms": round(result.latency_ms, 1), "tokens_in": result.tokens_in,
                    "tokens_out": result.tokens_out}
            if not result.ok:
                self.get_logger().error("turn failed " + json.dumps({**info, "reason": result.reason,
                                                                     "error": result.error[:300]}))
                self.pub.publish(make_discussion(text=failure_text(result), **ids))   # never silent
                return
            reply = result.reply
            if reply.kind == "proposal":
                msg = make_proposal(plan=reply.plan, text=reply.text, **ids)
            elif reply.kind == "agreement":
                msg = make_agreement(text=reply.text, **ids)
            else:
                msg = make_discussion(text=reply.text, **ids)
            self.pub.publish(msg)
            self.get_logger().info("turn " + json.dumps({**info, "kind": reply.kind, "repaired": result.repaired}))
        except Exception as e:   # a bug here must not leave the dialogue waiting either
            self.get_logger().error(f"turn crashed: {type(e).__name__}: {e}")
            try:
                self.pub.publish(make_discussion(text=failure_text("internal_error", f"{type(e).__name__}: {e}"),
                                                 **ids))
            except Exception as e2:
                self.get_logger().error(f"could not report the crash: {e2}")
        finally:
            self._busy.release()


def main(args=None) -> None:
    rclpy.init(args=args)
    try:
        node = AgentNode()
    except SettingsError as e:
        get_logger("mrs_agent").fatal(f"not starting: {e}")
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
