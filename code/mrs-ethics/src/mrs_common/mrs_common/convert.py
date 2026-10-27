"""Build and read mrs_msgs messages (Module 1). Requires a built workspace (imports mrs_msgs).

Every node should create messages through these helpers instead of filling fields by hand:
it keeps transaction_id / episode_id / stamps consistent, and plan_json always valid.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Optional

from builtin_interfaces.msg import Time
from mrs_msgs.msg import (DialogueTurn, EpisodeEvent, EthicalVerdict, ProposedPlan, TaskStatus,
                          ValidatedPlan, WorldState)

from .ids import new_transaction_id
from .plan_schema import Constraint, TeamPlan, constraints_from_json, constraints_to_json, plan_from_json, plan_to_json

VERDICTS = (EthicalVerdict.VERDICT_APPROVE, EthicalVerdict.VERDICT_MODIFY, EthicalVerdict.VERDICT_REJECT)
TASK_STATES = (TaskStatus.STATE_STARTED, TaskStatus.STATE_REACHED, TaskStatus.STATE_DONE,
               TaskStatus.STATE_FAILED, TaskStatus.STATE_TIMED_OUT)
EVENT_KINDS = (EpisodeEvent.KIND_START, EpisodeEvent.KIND_END, EpisodeEvent.KIND_VIOLATION,
               EpisodeEvent.KIND_TIMEOUT, EpisodeEvent.KIND_PLANNING_EXHAUSTED,
               EpisodeEvent.KIND_EDGE_BLOCKED, EpisodeEvent.KIND_EDGE_CLEARED)


def stamp_now(node=None) -> Time:
    """Node clock (sim time when use_sim_time=true) if a node is given, else wall clock."""
    if node is not None:
        return node.get_clock().now().to_msg()
    import time
    ns = time.time_ns()
    return Time(sec=ns // 1_000_000_000, nanosec=ns % 1_000_000_000)


# ---- plans -----------------------------------------------------------------
def make_proposed_plan(plan: TeamPlan, *, episode_id: str, attempt: int = 0,
                       transaction_id: Optional[str] = None, model_name: str = "", latency_ms: float = 0.0,
                       tokens_in: int = 0, tokens_out: int = 0, dialogue_digest: str = "",
                       node=None) -> ProposedPlan:
    return ProposedPlan(
        transaction_id=transaction_id or new_transaction_id(), episode_id=episode_id, attempt=int(attempt),
        plan_json=plan_to_json(plan), dialogue_digest=dialogue_digest, model_name=model_name,
        latency_ms=float(latency_ms), tokens_in=int(tokens_in), tokens_out=int(tokens_out),
        stamp=stamp_now(node))


def plan_from_msg(msg) -> TeamPlan:
    """TeamPlan from any message with a plan_json field (ProposedPlan, ValidatedPlan, DialogueTurn).
    Raises pydantic.ValidationError if the JSON does not match the schema."""
    if not getattr(msg, "plan_json", ""):
        raise ValueError(f"{type(msg).__name__} has an empty plan_json")
    return plan_from_json(msg.plan_json)


def make_validated_plan(plan: TeamPlan, *, transaction_id: str, episode_id: str, attempt: int,
                        source_verdict: str, node=None) -> ValidatedPlan:
    if source_verdict not in (EthicalVerdict.VERDICT_APPROVE, EthicalVerdict.VERDICT_MODIFY, "FALLBACK"):
        raise ValueError(f"source_verdict must be APPROVE, MODIFY or FALLBACK, got '{source_verdict}'")
    return ValidatedPlan(transaction_id=transaction_id, episode_id=episode_id, attempt=int(attempt),
                         plan_json=plan_to_json(plan), source_verdict=source_verdict, stamp=stamp_now(node))


# ---- verdicts --------------------------------------------------------------------
def make_verdict(*, transaction_id: str, episode_id: str, attempt: int, verdict: str,
                 rule_ids: Iterable[str] = (), standard_refs: Iterable[str] = (), risk_score: float = 0.0,
                 rationale: str = "", constraints: Iterable[Constraint] = (), latency_ms: float = 0.0,
                 node=None) -> EthicalVerdict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}, got '{verdict}'")
    return EthicalVerdict(
        transaction_id=transaction_id, episode_id=episode_id, attempt=int(attempt), verdict=verdict,
        rule_ids=list(rule_ids), standard_refs=list(standard_refs),
        risk_score=float(min(1.0, max(0.0, risk_score))), rationale=rationale,
        constraints_json=constraints_to_json(constraints), latency_ms=float(latency_ms), stamp=stamp_now(node))


def constraints_from_verdict(msg: EthicalVerdict) -> list[Constraint]:
    return constraints_from_json(msg.constraints_json)


# ---- dialogue, status, events ---------------------------------------------------------
def make_dialogue_turn(*, episode_id: str, speaker_id: str, text: str, round: int, attempt: int = 0,
                       transaction_id: str = "", plan: Optional[TeamPlan] = None, node=None) -> DialogueTurn:
    return DialogueTurn(episode_id=episode_id, transaction_id=transaction_id, attempt=int(attempt),
                        round=int(round), speaker_id=speaker_id, text=text, agree=plan is not None,
                        plan_json=plan_to_json(plan) if plan is not None else "", stamp=stamp_now(node))


def make_task_status(*, transaction_id: str, episode_id: str, robot_id: str, task_id: str, state: str,
                     waypoint: str = "", detail: str = "", node=None) -> TaskStatus:
    if state not in TASK_STATES:
        raise ValueError(f"state must be one of {TASK_STATES}, got '{state}'")
    return TaskStatus(transaction_id=transaction_id, episode_id=episode_id, robot_id=robot_id, task_id=task_id,
                      state=state, waypoint=waypoint, detail=detail, stamp=stamp_now(node))


def make_episode_event(*, episode_id: str, kind: str, robot_id: str = "", detail: Optional[dict] = None,
                       node=None) -> EpisodeEvent:
    if kind not in EVENT_KINDS:
        raise ValueError(f"kind must be one of {EVENT_KINDS}, got '{kind}'")
    return EpisodeEvent(episode_id=episode_id, kind=kind, robot_id=robot_id,
                        detail_json=json.dumps(detail or {}), stamp=stamp_now(node))


# ---- world state -> LLM summary ---------------------------------------------------------
def world_state_to_summary(msg: WorldState) -> dict[str, Any]:
    """The compact dict an agent puts in its prompt (format of the Module 3 example)."""
    summary: dict[str, Any] = {
        "robot_id": msg.robot_id,
        "at": msg.at,
        "zone": msg.zone,
        "battery": round(float(msg.battery), 2),
        "payload": msg.payload or None,
        "sectors": dict(zip(msg.sector_names, msg.sector_status)),
        "perceived": [{"type": e.type, "id": e.id, "at": e.at} for e in list(msg.nearby_humans) + list(msg.hazards)],
        "exits": {e: round(float(d), 1) for e, d in zip(msg.exit_ids, msg.exit_distances)},
    }
    return summary
