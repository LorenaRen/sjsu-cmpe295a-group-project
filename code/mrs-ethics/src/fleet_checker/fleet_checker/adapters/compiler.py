"""Plan compiler: a Module 1 TeamPlan + an mrs_sim map -> one checker Plan per robot.

The LLM names only targets; the compiler picks the route and the times, so a
proposer cannot shorten its own timings or skip a zone.

Route: shortest path on the waypoint graph (Dijkstra, edge length = Euclidean
distance). Routes of equal length (to 1e-9 m) are told apart by comparing node
names along the route, so the same input always gives the same route. Each
edge is driven straight at `speed`; the time spent in each zone comes from
where the edge crosses zone boundaries (SimMap.pieces).

Order: a robot does its tasks in list order, and depends_on may name tasks of
other robots. Both kinds of "waits for" form one graph, and tasks are
scheduled in topological order, each as early as what it waits for allows
(critical path method). A cycle in that graph is a deadlock. Module 1 only
checks that depends_on on its own is acyclic, so "a1 depends on b2, b1 depends
on a2" passes its validation and is reported here as DEPENDENCY_DEADLOCK.

ASSUMPTIONS, to be confirmed with Module 4 (planner) and Module 6 (executor).
The checker checks the timeline below; it is only right if the executor
behaves the same way.

  action         leaves when                                  done when
  navigate       previous task done and every depends_on done it arrives
  wait           previous task done (not waiting for deps)    it has arrived and every depends_on is done
  hold_position  like navigate                                it arrives; if it is the last task, the
                                                              robot stays there (claims' hold_final)

  - A hold_position that is not the robot's last task acts like navigate.
  - The executor drives the same shortest path, including how ties are broken.
  - depends_on is enforced by events (TaskStatus "done"), not by these times.
    The times are estimates: speed is nominal, and acceleration, turning on
    the spot and CBF slow-downs are not modelled. The cross-plan check's
    margin is what absorbs that error.

Task.priority is read into StepOrigin for logs only. It is not used to decide
who yields; that is R-1 and the fairness ledger.
"""
from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise
from typing import Mapping, NamedTuple

from mrs_common.plan_schema import Action as TaskAction, Task, TeamPlan

from ..plan import TIME_EPS, Action, Plan, PlanStep, TaskType, Violation, WorldSnapshot
from .sim_map import SimMap

# Length of the only step of a robot that never moves. A plan's last step is
# held to +inf by claims(hold_final=True), so this does not change S-1, P-1 or P-2.
PARKED_S = 1.0

_LENGTH_DIGITS = 9  # route lengths equal to 1e-9 m are ties, decided by node names


class StepOrigin(NamedTuple):
    """Where a compiled step came from, to translate a violation back to the TeamPlan."""
    task_id: str | None     # None: the robot has no task and stays where it is
    nodes: tuple[str, ...]  # graph nodes reached during the step; for a wait, where it waits
    priority: int | None    # Task.priority as proposed; kept for logs, never used for yielding


class TaskTiming(NamedTuple):
    depart: float   # leaves for the target
    arrive: float   # reaches the target
    done: float     # counts as done for depends_on


@dataclass(frozen=True)
class CompiledTeamPlan:
    plans: dict[str, Plan]                      # robot_id -> its plan
    origins: dict[str, tuple[StepOrigin, ...]]  # robot_id -> one origin per plan step
    timings: dict[str, TaskTiming]              # task_id -> when it runs
    snapshot: WorldSnapshot                     # t0 and the zone each robot starts in
    violations: tuple[Violation, ...]           # compile errors; when there are any, plans is empty


class _Segment(NamedTuple):
    zone: str
    d_start: float           # metres from the start of the task's route
    d_end: float
    nodes: tuple[str, ...]   # nodes reached inside this segment


def compile_team_plan(
    team_plan: TeamPlan,
    sim_map: SimMap,
    start_nodes: Mapping[str, str],
    *,
    speed: float,
    t0: float = 0.0,
    task_types: Mapping[str, TaskType] | None = None,
    plan_id: str = "teamplan",
    revision: int = 0,
) -> CompiledTeamPlan:
    """Compile every robot's tasks into a timed Plan.

    Args:
        start_nodes: robot_id -> the graph node it is at now. Every robot in the
            world, including robots the TeamPlan gives no task: they stay put,
            and a parked robot still blocks others.
        speed: nominal driving speed [m/s]. No default: Module 6's cruise speed
            is not decided yet.
        t0: current time on the simulation clock [s].
        task_types: robot_id -> TaskType. TeamPlan v1.1 has no task type, so
            robots not listed are DELIVERY; R-1's task_type tie-break then never
            decides, and full ties fall to robot_id order.
        plan_id, revision: ProposedPlan.transaction_id and attempt; each robot's
            Plan gets plan_id "<plan_id>/<robot_id>".

    Compile errors (unknown robot or target, no route, deadlock) come back as
    Violations, all of them at once, with no plans.
    """
    if speed <= 0:
        raise ValueError(f"speed must be > 0 m/s, got {speed}")
    for robot, node in start_nodes.items():
        if node not in sim_map.positions:
            raise ValueError(f"start node {node!r} of {robot!r} is not on map {sim_map.name!r}")
    snapshot = WorldSnapshot(
        time=t0, robot_regions={r: sim_map.zone_of[n] for r, n in start_nodes.items()})

    violations: list[Violation] = []
    routes: dict[str, list[_Segment]] = {}
    for robot in team_plan.robot_ids:
        tasks = team_plan.tasks_of(robot)
        if robot not in start_nodes:
            violations.append(Violation(
                code="UNKNOWN_ROBOT",
                message=f"robot {robot!r} has tasks but its position is unknown"))
            continue
        unknown = [t for t in tasks if t.target not in sim_map.positions]
        for t in unknown:
            violations.append(Violation(
                code="UNKNOWN_TARGET",
                message=f"task {t.task_id!r} ({robot}): target {t.target!r} is not a node on the map"))
        if unknown:
            continue
        here = start_nodes[robot]
        for t in tasks:
            path = _shortest_path(sim_map.neighbors, here, t.target)
            if path is None:
                violations.append(Violation(
                    code="NO_ROUTE",
                    message=f"task {t.task_id!r} ({robot}): no path from {here!r} to {t.target!r}"))
                break   # where the robot would be after this task is unknown
            routes[t.task_id] = _segments(sim_map, path)
            here = t.target

    order, cycle = _topological_order(team_plan)
    if cycle is not None:
        violations.append(Violation(code="DEPENDENCY_DEADLOCK",
                                    message=_describe_cycle(team_plan, cycle)))
    if violations:
        return CompiledTeamPlan({}, {}, {}, snapshot, tuple(violations))

    timings = _schedule(team_plan, order, routes, t0, speed)
    plans: dict[str, Plan] = {}
    origins: dict[str, tuple[StepOrigin, ...]] = {}
    for robot in sorted(start_nodes):
        steps, step_origins = _robot_steps(team_plan.tasks_of(robot), start_nodes[robot],
                                           routes, timings, sim_map, t0, speed)
        plans[robot] = Plan(
            plan_id=f"{plan_id}/{robot}",
            revision=revision,
            robot_id=robot,
            task_type=(task_types or {}).get(robot, TaskType.DELIVERY),
            steps=tuple(steps),
        )
        origins[robot] = tuple(step_origins)
    return CompiledTeamPlan(plans, origins, timings, snapshot, ())


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

def _shortest_path(neighbors: Mapping[str, Mapping[str, float]],
                   source: str, target: str) -> list[str] | None:
    """Dijkstra. Heap entries are (length, route), so among equal lengths the
    route whose node names sort first wins: deterministic, and easy to state."""
    heap: list[tuple[float, tuple[str, ...]]] = [(0.0, (source,))]
    finished: set[str] = set()
    while heap:
        length, route = heapq.heappop(heap)
        node = route[-1]
        if node in finished:
            continue
        if node == target:
            return list(route)
        finished.add(node)
        for nxt, edge in neighbors[node].items():
            if nxt not in finished:
                heapq.heappush(heap, (round(length + edge, _LENGTH_DIGITS), route + (nxt,)))
    return None


def _segments(sim_map: SimMap, path: list[str]) -> list[_Segment]:
    """Cut a route into one segment per zone visit; consecutive pieces in the same zone merge."""
    segments: list[_Segment] = []
    d = 0.0
    for u, v in pairwise(path):
        pieces = sim_map.pieces[(u, v)]
        for i, piece in enumerate(pieces):
            end = d + piece.length
            reached = (v,) if i == len(pieces) - 1 else ()
            if segments and segments[-1].zone == piece.zone:
                last = segments[-1]
                segments[-1] = last._replace(d_end=end, nodes=last.nodes + reached)
            else:
                segments.append(_Segment(piece.zone, d, end, reached))
            d = end
    return segments


# --------------------------------------------------------------------------
# Scheduling
# --------------------------------------------------------------------------

def _waits_for(team_plan: TeamPlan) -> dict[str, list[str]]:
    """task_id -> the tasks it waits for: its depends_on plus the robot's previous task."""
    out: dict[str, list[str]] = {}
    for a in team_plan.assignments:
        previous: str | None = None
        for t in a.tasks:
            out[t.task_id] = sorted(set(t.depends_on) | ({previous} if previous else set()))
            previous = t.task_id
    return out


def _topological_order(team_plan: TeamPlan) -> tuple[list[str], list[str] | None]:
    """Kahn's algorithm. Returns (order, None), or (partial order, cycle) when the
    tasks cannot all be ordered; cycle[i] waits for cycle[i + 1], last == first."""
    waits_for = _waits_for(team_plan)
    remaining = {t: len(w) for t, w in waits_for.items()}
    dependents: dict[str, list[str]] = defaultdict(list)
    for t, ws in waits_for.items():
        for w in ws:
            dependents[w].append(t)
    ready = [t for t, n in remaining.items() if n == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        t = heapq.heappop(ready)
        order.append(t)
        for d in dependents[t]:
            remaining[d] -= 1
            if remaining[d] == 0:
                heapq.heappush(ready, d)
    if len(order) == len(waits_for):
        return order, None
    # Every task left still waits for another task left, so walking "waits for"
    # among them must come back to a task already visited.
    left = {t for t, n in remaining.items() if n > 0}
    walk = [min(left)]
    while True:
        nxt = min(w for w in waits_for[walk[-1]] if w in left)
        if nxt in walk:
            return order, walk[walk.index(nxt):] + [nxt]
        walk.append(nxt)


def _describe_cycle(team_plan: TeamPlan, cycle: list[str]) -> str:
    parts = []
    for waiter, waited in pairwise(cycle):
        if waited in team_plan.task(waiter).depends_on:
            parts.append(f"{waiter} depends on {waited}")
        else:
            parts.append(f"{waiter} comes after {waited} on {team_plan.robot_of(waiter)}")
    return "tasks wait for each other in a cycle: " + "; ".join(parts)


def _schedule(team_plan: TeamPlan, order: list[str], routes: Mapping[str, list[_Segment]],
              t0: float, speed: float) -> dict[str, TaskTiming]:
    tasks = {t.task_id: t for a in team_plan.assignments for t in a.tasks}
    previous = {b.task_id: a.task_id
                for asg in team_plan.assignments for a, b in pairwise(asg.tasks)}
    timings: dict[str, TaskTiming] = {}
    for task_id in order:
        task = tasks[task_id]
        ready = timings[previous[task_id]].done if task_id in previous else t0
        deps_done = max((timings[d].done for d in task.depends_on), default=ready)
        length = routes[task_id][-1].d_end if routes[task_id] else 0.0
        if task.action == TaskAction.WAIT:
            depart = ready
            arrive = depart + length / speed
            done = deps_done if deps_done - arrive > TIME_EPS else arrive
        else:
            depart = deps_done if deps_done - ready > TIME_EPS else ready
            arrive = depart + length / speed
            done = arrive
        timings[task_id] = TaskTiming(depart, arrive, done)
    return timings


def _robot_steps(tasks: list[Task], start: str, routes: Mapping[str, list[_Segment]],
                 timings: Mapping[str, TaskTiming], sim_map: SimMap,
                 t0: float, speed: float) -> tuple[list[PlanStep], list[StepOrigin]]:
    steps: list[PlanStep] = []
    origins: list[StepOrigin] = []

    def add(action: Action, zone: str, t_start: float, t_end: float,
            task: Task, nodes: tuple[str, ...]) -> None:
        steps.append(PlanStep(action=action, region=zone, t_start=t_start, t_end=t_end))
        origins.append(StepOrigin(task.task_id, nodes, task.priority))

    here, clock = start, t0
    for task in tasks:
        tm = timings[task.task_id]
        if tm.depart > clock:    # navigate / hold_position: waits in place for depends_on
            add(Action.WAIT, sim_map.zone_of[here], clock, tm.depart, task, (here,))
        for seg in routes[task.task_id]:
            add(Action.NAVIGATE, seg.zone, tm.depart + seg.d_start / speed,
                tm.depart + seg.d_end / speed, task, seg.nodes)
        if tm.done > tm.arrive:  # wait: has arrived, waits there for depends_on
            add(Action.WAIT, sim_map.zone_of[task.target], tm.arrive, tm.done, task, (task.target,))
        here, clock = task.target, tm.done

    if not steps:   # never moves: no tasks, or only tasks at the node it is already at
        last = tasks[-1] if tasks else None
        steps.append(PlanStep(action=Action.WAIT, region=sim_map.zone_of[start],
                              t_start=t0, t_end=t0 + PARKED_S))
        origins.append(StepOrigin(last.task_id if last else None, (start,),
                                  last.priority if last else None))
    return steps, origins
