"""How the fake planner changes a TeamPlan for the constraints of a REJECT (pure Python, no ROS).

The fake planner knows the waypoint graph, the constraints and where its robots start, nothing
else. It reads no zone type and no rule, so what it manages to fix shows how far the gate's
constraints alone can guide a replan. It imports nothing from the checker's own checks (fleet_checker's verdict,
team_check, cross_plan): a test tool that shared their code would share their bugs, and the
loop would pass anyway.

  order_before (task_id K, value Y)  add K to Y's depends_on
  forbid_node  (robot r, task T)     T ends at one of the nodes (P-1: the plan ends there):
                                     retarget T. Otherwise, if r stands at one of them before T,
                                     waiting for T's depends_on (S-4) where the task before T
                                     ends, or where r starts if T is its first task: insert a
                                     navigate to another node before T, so that r waits there;
                                     T is unchanged
  avoid_node   (robot r)             retarget every task of r that goes to an avoided node. If
                                     none does, r's route crosses the zone, and a TeamPlan cannot
                                     choose the route: it has no via-points, the route to each
                                     target is the shortest path (rules_v1.md, assumption 2)
  custom, other kinds                not applied: a custom constraint is text for an LLM

forbid_node is taken per (robot, task) and avoid_node per robot, because P-1 and P-2 name every
node of the zone, not only the one the plan uses. Each constraint ends up
  changed      the plan was changed for it
  noted        nothing in the plan has to change for it, but its node is kept out of r's tasks
               from now on
  not_applied  the fake planner cannot do what it asks; why_not says why

A retarget, or an inserted waiting point, goes to the nearest allowed node: the shortest path
length on the waypoint graph from the refused node (ties: by node name) that is not in r's
banned set, and not where another robot ends its plan (the target of its last task, or where it
starts if it has no task): two robots cannot both stay at one node. The banned set is every
node a forbid_node or avoid_node has named for r so far in this planning cycle; the caller
passes it from one REJECT to the next, so that a robot is never sent back to a node already
refused. Crossing a banned node on the way is fine: the distance only ranks candidates, it is
not the route.

Where its robots start (the scenario's start nodes, as a planner knows where its robots are) is
used only to recognise a robot waiting where it starts.

Every change is monotone (depends_on only grows, a retarget never returns to a banned node, an
insertion only adds a task), so the fake planner never proposes a plan it has proposed before.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from mrs_common.plan_schema import Constraint, TeamPlan
from pydantic import ValidationError

CHANGED = "changed"
NOTED = "noted"
NOT_APPLIED = "not_applied"

Graph = Mapping[str, Mapping[str, float]]   # node -> {neighbour: edge length [m]}, as SimMap.neighbors

_MAX_TASK_ID = 64   # mrs_common's Task.task_id max_length
_DISTANCE_DIGITS = 9   # metres: path lengths that differ only by rounding are a tie

_CUSTOM = "a custom constraint is text for an LLM; the fake planner does not read text"


@dataclass(frozen=True)
class Outcome:
    """What one constraint did to the plan."""
    constraint: Constraint
    result: str                  # CHANGED, NOTED or NOT_APPLIED
    change: str | None = None    # CHANGED: what changed, e.g. "b2.depends_on += a1"
    why_not: str | None = None   # NOT_APPLIED: why

    def as_record(self) -> dict[str, Any]:
        """The constraint's fields and what it did, for the logs; empty fields are left out."""
        record = self.constraint.model_dump(mode="json", exclude_none=True)
        record["result"] = self.result
        if self.change is not None:
            record["change"] = self.change
        if self.why_not is not None:
            record["why_not"] = self.why_not
        return record


@dataclass(frozen=True)
class Repair:
    plan: TeamPlan                       # the next plan; equal to the one given if nothing changed
    outcomes: tuple[Outcome, ...]        # one for each constraint, in their order
    banned: dict[str, frozenset[str]]    # robot -> nodes none of its tasks may go to, from now on

    @property
    def changes(self) -> tuple[str, ...]:
        return tuple(o.change for o in self.outcomes if o.result == CHANGED)


def apply_constraints(plan: TeamPlan, constraints: Sequence[Constraint], graph: Graph,
                      banned: Mapping[str, frozenset[str]] | None = None,
                      start_nodes: Mapping[str, str] | None = None) -> Repair:
    """Apply the constraints of one REJECT to plan. `banned` is the previous REJECT's
    Repair.banned (None for the first); start_nodes, robot -> where it starts (None: not known).
    The plan given is not modified."""
    edit = _Edit(plan, graph, banned or {}, constraints, start_nodes or {})
    outcomes: dict[int, Outcome] = {}
    forbid: dict[tuple[str | None, str | None], list[int]] = {}
    avoid: dict[str | None, list[int]] = {}
    for i, c in enumerate(constraints):
        if c.kind == "order_before":
            outcomes[i] = edit.order_before(c)   # one at a time: each checked with those before it
        elif c.kind == "forbid_node":
            forbid.setdefault((c.robot_id, c.task_id), []).append(i)
        elif c.kind == "avoid_node":
            avoid.setdefault(c.robot_id, []).append(i)
        elif c.kind == "custom":
            outcomes[i] = Outcome(c, NOT_APPLIED, why_not=_CUSTOM)
        else:
            outcomes[i] = Outcome(c, NOT_APPLIED, why_not=f"the fake planner does not handle {c.kind} "
                                                          "constraints (the gate does not send them yet)")
    for (robot, task_id), group in forbid.items():
        outcomes.update(edit.forbid_node(robot, task_id, {i: constraints[i] for i in group}))
    for robot, group in avoid.items():
        outcomes.update(edit.avoid_node(robot, {i: constraints[i] for i in group}))

    ordered = tuple(outcomes[i] for i in range(len(constraints)))
    new_plan = TeamPlan.model_validate(edit.data) if any(o.result == CHANGED for o in ordered) else plan
    return Repair(new_plan, ordered, {r: frozenset(n) for r, n in edit.banned.items() if n})


class _Edit:
    """The plan being changed, as plain data, and the nodes each robot may not be sent to."""

    def __init__(self, plan: TeamPlan, graph: Graph, banned: Mapping[str, frozenset[str]],
                 constraints: Sequence[Constraint], start_nodes: Mapping[str, str]):
        self.data = plan.model_dump(mode="json")
        self.graph = graph
        self.start = dict(start_nodes)
        self.tasks = {t["task_id"]: t for a in self.data["assignments"] for t in a["tasks"]}
        self.owner = {t["task_id"]: a["robot_id"] for a in self.data["assignments"] for t in a["tasks"]}
        self.task_lists = {a["robot_id"]: a["tasks"] for a in self.data["assignments"]}
        # As proposed, before any change: which task goes where, and in what order.
        self.target_before = {task_id: t["target"] for task_id, t in self.tasks.items()}
        self.task_order = {robot: [t["task_id"] for t in tasks] for robot, tasks in self.task_lists.items()}
        self.retargeted: set[str] = set()
        # Every node this REJECT names joins the banned set first, so that no retarget below
        # goes to a node another constraint of the same REJECT refuses.
        self.banned = {robot: set(nodes) for robot, nodes in banned.items()}
        for c in constraints:
            if c.kind in ("forbid_node", "avoid_node") and c.robot_id and c.node:
                self.banned.setdefault(c.robot_id, set()).add(c.node)

    # ---- order_before ---------------------------------------------------------------------
    def order_before(self, c: Constraint) -> Outcome:
        if c.task_id is None or c.value is None:
            return Outcome(c, NOT_APPLIED, why_not="it does not name both tasks")
        missing = [t for t in (c.task_id, c.value) if t not in self.tasks]
        if missing:
            return Outcome(c, NOT_APPLIED, why_not=f"there is no task {missing[0]} in the plan")
        later = self.tasks[c.value]
        if c.task_id in later["depends_on"]:
            return Outcome(c, NOT_APPLIED, why_not=f"{c.value} already depends on {c.task_id}")
        later["depends_on"].append(c.task_id)
        try:
            TeamPlan.model_validate(self.data)
        except ValidationError as e:
            later["depends_on"].pop()
            return Outcome(c, NOT_APPLIED, why_not=f"it would make the plan invalid: {e.errors()[0]['msg']}")
        return Outcome(c, CHANGED, change=f"{c.value}.depends_on += {c.task_id}")

    # ---- forbid_node ----------------------------------------------------------------------
    def forbid_node(self, robot: str | None, task_id: str | None,
                    group: dict[int, Constraint]) -> dict[int, Outcome]:
        def none_applied(why_not: str) -> dict[int, Outcome]:
            return {i: Outcome(c, NOT_APPLIED, why_not=why_not) for i, c in group.items()}

        if task_id is None:
            return none_applied(f"it names no task: {robot} has no task to change "
                                "(a robot without tasks stands at its start node)")
        if task_id not in self.tasks:
            return none_applied(f"there is no task {task_id} in the plan")
        if self.owner[task_id] != robot:
            return none_applied(f"task {task_id} is {self.owner[task_id]}'s, not {robot}'s")
        nodes = {c.node for c in group.values()}
        target = self.target_before[task_id]
        if target in nodes:                                   # the task ends there
            return self._retargeted(robot, group, {task_id})
        before = self._task_before(robot, task_id)
        if before is not None and self.target_before[before] in nodes:   # waits there before the task
            return self._waits_elsewhere(robot, task_id, self.target_before[before], group)
        if before is None and self.start.get(robot) in nodes:            # waits where it starts
            return self._waits_elsewhere(robot, task_id, self.start[robot], group)
        where = f"the task before it, {before}, ends at {self.target_before[before]}" if before \
            else f"it is {robot}'s first task"
        return {i: Outcome(c, NOT_APPLIED, why_not=f"task {task_id} does not stop at {c.node}: it goes to "
                                                   f"{target}, and {where}")
                for i, c in group.items()}

    def _waits_elsewhere(self, robot: str, task_id: str, node: str,
                         group: dict[int, Constraint]) -> dict[int, Outcome]:
        where = self._nearest(robot, node)
        outcomes = {i: Outcome(c, NOTED) for i, c in group.items() if c.node != node}
        if where is None:
            outcomes.update({i: Outcome(c, NOT_APPLIED, why_not=f"no node left that {robot} may be sent to")
                             for i, c in group.items() if c.node == node})
            return outcomes
        later = self.tasks[task_id]
        wait_id = self._new_task_id(f"{task_id[:_MAX_TASK_ID - 8]}_wait")
        inserted = {"task_id": wait_id, "action": "navigate", "target": where, "priority": later["priority"],
                    "justification": f"Inserted by the fake planner: wait here, not at {node}, until task "
                                     f"{task_id} may start.",
                    "depends_on": []}
        tasks = self.task_lists[robot]
        tasks.insert(tasks.index(later), inserted)
        self.tasks[wait_id], self.owner[wait_id] = inserted, robot
        change = f"{wait_id}: navigate {where} before {task_id}, to wait there instead of at {node}"
        outcomes.update({i: Outcome(c, CHANGED, change=change) for i, c in group.items() if c.node == node})
        return outcomes

    # ---- avoid_node -----------------------------------------------------------------------
    def avoid_node(self, robot: str | None, group: dict[int, Constraint]) -> dict[int, Outcome]:
        if robot is None:
            return {i: Outcome(c, NOT_APPLIED, why_not="it names no robot") for i, c in group.items()}
        nodes = {c.node for c in group.values()}
        going = {t for t in self.task_order.get(robot, []) if self.target_before[t] in nodes}
        if not going:
            return {i: Outcome(c, NOT_APPLIED,
                               why_not=f"no task of {robot} goes to {c.node} or to another node this REJECT "
                                       f"avoids, so it is {robot}'s route that crosses the zone, and a "
                                       "TeamPlan cannot choose the route (it is the shortest path)")
                    for i, c in group.items()}
        return self._retargeted(robot, group, going)

    # ---- shared ---------------------------------------------------------------------------
    def _retargeted(self, robot: str, group: dict[int, Constraint], task_ids: set[str]) -> dict[int, Outcome]:
        """Retarget task_ids (once each per REJECT); the constraint naming a task's old target is
        changed by it (noted if an earlier constraint already moved the task), the others noted."""
        changed: dict[str, list[str]] = {}   # old target -> changes; [] if no node was left
        for task_id in sorted(task_ids):
            old = self.target_before[task_id]
            if task_id in self.retargeted:
                continue
            new = self._nearest(robot, old)
            changed.setdefault(old, [])
            if new is not None:
                self.tasks[task_id]["target"] = new
                self.retargeted.add(task_id)
                changed[old].append(f"{task_id}.target {old} -> {new}")
        outcomes = {}
        for i, c in group.items():
            if c.node not in changed:
                outcomes[i] = Outcome(c, NOTED)
            elif not changed[c.node]:
                outcomes[i] = Outcome(c, NOT_APPLIED, why_not=f"no node left that {robot} may be sent to")
            else:
                outcomes[i] = Outcome(c, CHANGED, change="; ".join(changed[c.node]))
        return outcomes

    def _where_others_end(self, robot: str) -> set[str]:
        """Where every other robot ends its plan, as the plan stands now: its last task's target,
        or where it starts if it has no task."""
        ends = {tasks[-1]["target"] for other, tasks in self.task_lists.items() if other != robot and tasks}
        return ends | {node for other, node in self.start.items()
                       if other != robot and not self.task_lists.get(other)}

    def _task_before(self, robot: str, task_id: str) -> str | None:
        ids = self.task_order[robot]
        i = ids.index(task_id)
        return ids[i - 1] if i > 0 else None

    def _new_task_id(self, base: str) -> str:
        task_id, n = base, 2
        while task_id in self.tasks:
            task_id, n = f"{base}{n}", n + 1
        return task_id

    def _nearest(self, robot: str, start: str) -> str | None:
        """The node nearest to start along the graph that robot may be sent to (ties: by name)."""
        if start not in self.graph:
            return None
        banned = self.banned.get(robot, set()) | self._where_others_end(robot)
        best = {start: 0.0}
        heap = [(0.0, start)]
        while heap:
            d, node = heapq.heappop(heap)
            if d > best[node]:
                continue
            if node != start and node not in banned:
                return node
            for neighbour, length in self.graph[node].items():
                nd = round(d + length, _DISTANCE_DIGITS)
                if nd < best.get(neighbour, math.inf):
                    best[neighbour] = nd
                    heapq.heappush(heap, (nd, neighbour))
        return None
