"""TeamPlan schema: the content of every plan_json field (Module 1).

ROS messages stay frozen; this pydantic model is where the plan format can evolve.
Pure Python (no ROS imports), so it is usable and testable anywhere.

    TeamPlan
      assignments: list[Assignment]
    Assignment
      robot_id: str
      tasks: list[Task]
    Task
      task_id, action, target, priority (1 = highest), justification
      depends_on (optional): task_ids that must be done before this one starts

Bump SCHEMA_VERSION whenever a field changes meaning, and say so in the PR.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.1"  # 1.1: navigation-only actions (assist, carry removed)

_ID_PATTERN = r"^[A-Za-z][A-Za-z0-9_\-]*$"


class Action(str, Enum):
    """Navigation only: robots move between graph nodes and stop; they do not manipulate or assist."""
    NAVIGATE = "navigate"            # drive to target, then the next task starts
    WAIT = "wait"                    # drive to target and wait there until depends_on tasks are done
    HOLD_POSITION = "hold_position"  # drive to target and stay until further notice (also the safe fallback)


# Actions that leave the robot standing at its target: what keep-clear rules check.
STATIONARY_ACTIONS = frozenset({Action.WAIT, Action.HOLD_POSITION})


class _Strict(BaseModel):
    # Unknown keys are an error: an LLM inventing fields should fail validation loudly.
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Task(_Strict):
    task_id: str = Field(pattern=_ID_PATTERN, max_length=64)
    action: Action
    target: str = Field(min_length=1, max_length=64, description="a node name on the waypoint graph")
    priority: int = Field(ge=1, le=10, description="1 = highest")
    justification: str = Field(default="", max_length=1000, description="the agent's stated reason")
    depends_on: list[str] = Field(default_factory=list, description="task_ids that must finish first")


class Assignment(_Strict):
    robot_id: str = Field(pattern=_ID_PATTERN, max_length=64)
    tasks: list[Task] = Field(default_factory=list)


class TeamPlan(_Strict):
    schema_version: str = SCHEMA_VERSION
    assignments: list[Assignment] = Field(min_length=1)
    rationale: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _consistency(self) -> "TeamPlan":
        robots = [a.robot_id for a in self.assignments]
        dup_r = {r for r in robots if robots.count(r) > 1}
        if dup_r:
            raise ValueError(f"robot(s) assigned more than once: {sorted(dup_r)}")
        ids = [t.task_id for a in self.assignments for t in a.tasks]
        dup_t = {t for t in ids if ids.count(t) > 1}
        if dup_t:
            raise ValueError(f"duplicate task_id(s): {sorted(dup_t)}")
        known = set(ids)
        for a in self.assignments:
            for t in a.tasks:
                if t.task_id in t.depends_on:
                    raise ValueError(f"task '{t.task_id}' depends on itself")
                missing = [d for d in t.depends_on if d not in known]
                if missing:
                    raise ValueError(f"task '{t.task_id}' depends on unknown task(s) {missing}")
        _check_acyclic(self)
        return self

    # ---- convenience ------------------------------------------------------
    def tasks_of(self, robot_id: str) -> list[Task]:
        for a in self.assignments:
            if a.robot_id == robot_id:
                return a.tasks
        return []

    def task(self, task_id: str) -> Optional[Task]:
        for a in self.assignments:
            for t in a.tasks:
                if t.task_id == task_id:
                    return t
        return None

    def robot_of(self, task_id: str) -> Optional[str]:
        for a in self.assignments:
            if any(t.task_id == task_id for t in a.tasks):
                return a.robot_id
        return None

    @property
    def robot_ids(self) -> list[str]:
        return [a.robot_id for a in self.assignments]


def _check_acyclic(plan: TeamPlan) -> None:
    deps = {t.task_id: list(t.depends_on) for a in plan.assignments for t in a.tasks}
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(n: str, path: list[str]) -> None:
        if state.get(n) == 2:
            return
        if state.get(n) == 1:
            raise ValueError(f"circular depends_on: {' -> '.join(path + [n])}")
        state[n] = 1
        for d in deps.get(n, []):
            visit(d, path + [n])
        state[n] = 2

    for n in deps:
        visit(n, [])


# --------------------------------------------------------------------------
# Constraints carried in EthicalVerdict.constraints_json (Module 5 -> Module 4)
# --------------------------------------------------------------------------
ConstraintKind = Literal[
    "forbid_node",     # never stop/wait at `node` (e.g. a node inside a keep-clear zone)
    "avoid_node",      # do not path through `node`
    "avoid_edge",      # do not travel edge `value` (undirected id from names.edge_id), e.g. a blocked corridor
    "replace_target",  # in task `task_id`, use `value` instead of the current target
    "order_before",    # task `task_id` must finish before task `value` starts
    "reassign",        # give task `task_id` to robot `value`
    "custom",          # anything else; `text` must explain it
]


class Constraint(_Strict):
    kind: ConstraintKind
    rule_id: str = Field(min_length=1, description="rule that produced this constraint")
    robot_id: Optional[str] = None
    task_id: Optional[str] = None
    node: Optional[str] = None
    value: Optional[str] = None
    text: str = Field(min_length=1, description="plain-language version, appended to the LLM prompt")

    @model_validator(mode="after")
    def _edge_value(self) -> "Constraint":
        if self.kind == "avoid_edge":
            from .names import edge_nodes  # pure Python
            if not self.value:
                raise ValueError("avoid_edge needs value = an edge id from names.edge_id()")
            edge_nodes(self.value)  # raises ValueError if not a canonical edge id
        return self


def constraints_to_json(constraints: Iterable[Constraint]) -> str:
    return json.dumps([c.model_dump(mode="json", exclude_none=True) for c in constraints])


def constraints_from_json(text: str) -> list[Constraint]:
    if not text or not text.strip():
        return []
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("constraints_json must be a JSON list")
    return [Constraint.model_validate(c) for c in data]


# --------------------------------------------------------------------------
# JSON helpers
# --------------------------------------------------------------------------
def plan_to_json(plan: TeamPlan) -> str:
    """Canonical serialisation used in every plan_json field."""
    return plan.model_dump_json()


def plan_from_json(text: str) -> TeamPlan:
    return TeamPlan.model_validate_json(text)


def team_plan_json_schema() -> dict:
    """JSON Schema for LLM structured-output / JSON mode (Module 4)."""
    return TeamPlan.model_json_schema()


def check_references(plan: TeamPlan, robot_ids: Iterable[str], graph_nodes: Iterable[str]) -> list[str]:
    """Grounding check against the scenario: unknown robots or targets.

    Returns human-readable problems (empty list = OK). Shared by the planner (to give the LLM
    a precise error to fix) and the gatekeeper (to reject hallucinated places).
    """
    robots, nodes = set(robot_ids), set(graph_nodes)
    problems = [f"unknown robot '{a.robot_id}'" for a in plan.assignments if a.robot_id not in robots]
    for a in plan.assignments:
        for t in a.tasks:
            if t.target not in nodes:
                problems.append(f"task '{t.task_id}': unknown target '{t.target}'")
    return problems


def hold_all_plan(robot_ids: Iterable[str], targets: dict[str, str], reason: str) -> TeamPlan:
    """Safe fallback plan: every robot holds where it is (Module 4, after attempts are exhausted).

    `targets` maps robot_id -> its current graph node.
    """
    return TeamPlan(
        assignments=[Assignment(robot_id=r, tasks=[Task(task_id=f"hold_{r}", action=Action.HOLD_POSITION,
                                                         target=targets[r], priority=1, justification=reason)])
                     for r in robot_ids],
        rationale=f"fallback: {reason}",
    )
