"""An agent's reply to one turn: parsed, validated and grounded on the map (Module 4). Pure Python.

Each agent proposes only its own robot's part of the team plan (parts.py). The LLM answers with
one JSON object:

  {"kind": "proposal",   "text": "...", "plan": {"assignments": [{"robot_id": <me>, "tasks": [...]}]}}
  {"kind": "agreement",  "text": "..."}          accept the current team plan
  {"kind": "discussion", "text": "..."}          talk: a question, an objection, a fact

parse_reply() takes the speaker's own assignment from the reply, merges it into the current team
plan (the teammates' latest parts), and validates the result: format, unique task ids,
depends_on naming existing tasks, no cycles, robots and nodes of the map, and the speaker's own
mission goal. It returns a TurnReply whose plan is that merged team plan (what the node
publishes, so every DialogueTurn carries a complete, valid TeamPlan), or raises ReplyError with
one short message the agent sends back to the LLM once (Module 4, Structured output).

Lenient on purpose, because the meaning is plain (phi3.5 does all of these):
  - an agreement or discussion that carries a "plan": the plan is dropped, the kind decides;
  - a proposal with assignments for teammates: only the speaker's own is used (each robot speaks
    for itself);
  - the speaker listed in several assignments: their tasks are joined, in order.
"""
from __future__ import annotations

import json
from typing import Literal, Mapping, Optional, Sequence

from mrs_common.plan_schema import SCHEMA_VERSION, TeamPlan, check_references
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .mission import check_goals
from .parts import merge, part_of

KINDS = ("proposal", "agreement", "discussion")


class ReplyError(ValueError):
    """The reply cannot be used; str(e) is what the LLM is told to fix."""


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["proposal", "agreement", "discussion"]
    text: str = Field(default="", max_length=2000)
    plan: Optional[dict] = None

    @model_validator(mode="before")
    @classmethod
    def _drop_plan_unless_proposal(cls, data):
        if isinstance(data, dict) and data.get("kind") in ("agreement", "discussion") and "plan" in data:
            return {k: v for k, v in data.items() if k != "plan"}
        return data

    @model_validator(mode="after")
    def _proposal_has_plan(self) -> "_Envelope":
        if self.kind == "proposal" and self.plan is None:
            raise ValueError("a proposal must include 'plan' with your robot's part")
        return self


class TurnReply(BaseModel):
    """A usable reply. For a proposal: `part` is the speaker's own assignment, `plan` the team plan
    with that part merged in."""
    kind: Literal["proposal", "agreement", "discussion"]
    text: str = ""
    part: Optional[dict] = None
    plan: Optional[TeamPlan] = None


def extract_json(raw: str) -> dict:
    """The JSON object in an LLM answer: the whole text, or the outermost {...} inside it
    (models sometimes wrap JSON in prose or a ``` block)."""
    text = raw.strip()
    try:
        value = json.loads(text)
    except ValueError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ReplyError("the reply is not JSON: answer with exactly one JSON object") from None
        try:
            value = json.loads(text[start:end + 1])
        except ValueError as e:
            raise ReplyError(f"the reply is not valid JSON ({e.msg} at character {e.pos})") from None
    if not isinstance(value, dict):
        raise ReplyError("the reply must be one JSON object, not a list or a value")
    return value


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    where = ".".join(str(p) for p in err["loc"])
    msg = err["msg"].removeprefix("Value error, ")
    return f"{where}: {msg}" if where else msg


def reply_schema(robot_id: str, graph_nodes: Sequence[str], teammate_task_ids: Sequence[str] = ()) -> dict:
    """The JSON schema of this agent's reply, this turn, for backends that enforce a schema while
    generating (constrained decoding: the model cannot produce anything else). It fixes what can
    be fixed by shape: the reply kinds, this robot's id, task ids starting with its letter, map
    nodes as targets, actions, and depends_on naming its own tasks or teammates' current ones.
    Every text field (text, justification, rationale) is required, so even a small model always
    says why. What a schema cannot express (goals, cycles, rules) is still checked by
    parse_reply() and the gate."""
    letter = robot_id[-1]
    own_id = {"type": "string", "pattern": f"^{letter}[0-9]+$"}
    dep = {"anyOf": [own_id, {"enum": sorted(teammate_task_ids)}]} if teammate_task_ids else own_id
    task = {"type": "object", "additionalProperties": False,
            "properties": {"task_id": own_id,
                           "action": {"enum": ["navigate", "wait", "hold_position"]},
                           "target": {"enum": sorted(graph_nodes)},
                           "priority": {"type": "integer", "minimum": 1, "maximum": 10},
                           "justification": {"type": "string"},
                           "depends_on": {"type": "array", "items": dep}},
            "required": ["task_id", "action", "target", "priority", "justification", "depends_on"]}
    part = {"type": "object", "additionalProperties": False,
            "properties": {"robot_id": {"enum": [robot_id]},
                           "tasks": {"type": "array", "minItems": 1, "items": task}},
            "required": ["robot_id", "tasks"]}
    plan = {"type": "object", "additionalProperties": False,
            "properties": {"assignments": {"type": "array", "minItems": 1, "maxItems": 1, "items": part},
                           "rationale": {"type": "string"},
                           "schema_version": {"enum": [SCHEMA_VERSION]}},   # optional
            "required": ["assignments", "rationale"]}
    return {"anyOf": [
        {"type": "object", "additionalProperties": False,
         "properties": {"kind": {"enum": ["proposal"]}, "text": {"type": "string"}, "plan": plan},
         "required": ["kind", "text", "plan"]},
        {"type": "object", "additionalProperties": False,
         "properties": {"kind": {"enum": ["agreement", "discussion"]}, "text": {"type": "string"}},
         "required": ["kind", "text"]},
    ]}


def parse_reply(raw: str, *, robot_id: str, team: Sequence[str], graph_nodes: Sequence[str],
                others: Optional[Mapping[str, dict]] = None, goals: Optional[Mapping] = None) -> TurnReply:
    """robot_id: the speaker. others: teammates' current parts (parts.TeamView.parts; the speaker's
    own entry, if any, is replaced). goals: robot -> mission.Goal; only the speaker's is checked."""
    try:
        env = _Envelope.model_validate(extract_json(raw))
    except ValidationError as e:
        raise ReplyError(_first_error(e)) from None
    if env.kind != "proposal":
        return TurnReply(kind=env.kind, text=env.text)

    part = part_of(env.plan, robot_id)
    if part is None:
        raise ReplyError(f"your proposal must contain your own part: an assignment with \"robot_id\": "
                         f"\"{robot_id}\" and its tasks. Propose only {robot_id}'s tasks; your teammates "
                         f"propose theirs.")
    parts = {r: p for r, p in (others or {}).items() if r != robot_id}
    parts[robot_id] = part
    try:
        plan = TeamPlan.model_validate(merge(parts, team, str(env.plan.get("rationale", ""))))
    except ValidationError as e:
        msg = _first_error(e)
        if "duplicate task_id" in msg:
            msg += f" (a teammate already uses it: start your task ids with your robot's letter, e.g. {robot_id[-1]}1)"
        elif "depends on unknown task" in msg:
            msg += " (depends_on may only name tasks in your part or in the current team plan)"
        raise ReplyError(msg) from None

    problems = check_references(plan, team, graph_nodes)
    if problems:
        raise ReplyError("; ".join(problems) + ". Use only node names from the map.")
    own_goal = {robot_id: goals[robot_id]} if goals and robot_id in goals else {}
    missed = check_goals(plan, own_goal)
    if missed:
        raise ReplyError("; ".join(missed) + ". Your last task must end at your mission goal.")
    return TurnReply(kind="proposal", text=env.text, part=part, plan=plan)
