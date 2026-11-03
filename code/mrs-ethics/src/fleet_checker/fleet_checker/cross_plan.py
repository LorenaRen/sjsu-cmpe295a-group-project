"""Cross-plan conflict detection for the shared checking layer.

S-1: Interval capacity violation
    Same region or resource occupied by more robots than its `capacity` allows
    during any overlapping time window.

    When two robots swap adjacent regions head-on (A→B while B→A), the detector
    labels it `EDGE_SWAP` instead of `CAPACITY_EXCEEDED`.

S-2: Human crowding
    More than N robots within R metres of the same person at once. Each
    person's surroundings are one more resource, of capacity N: the claims
    come from crowding.py, the sweep is S-1's (sweep_over_capacity), and the
    conflicts go to the same R-1.

S-4: Node occupancy
    Two robots standing still at the same node at once. Each node is a
    resource of capacity 1, with claims from plan.stop_claims (standing
    steps and where each plan ends); the same sweep and the same R-1. Stops
    in a zone of capacity 1 are left to S-1.

R-1: Yield assignment
    When a conflict is detected, decide which robots yield (n robots in a
    capacity-c region: n - c of them) based on:
    1. Fewer cumulative yields (from FairnessLedger) yields this time
    2. Tie-break: task_type rank — SERVICE yields to DELIVERY
    3. Tie-break: the robot that reaches a conflict region later yields
       (one entry time per robot for the whole round, see first_entry_times)
    4. Tie-break: robot_id

F-2: Starvation override
    Starving robots are kept ahead of everyone else; they yield only when
    there are more starving robots than capacity, ranked among themselves by R-1.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Mapping, NamedTuple, Sequence

from .crowding import CrowdingLimit, crowding_claims
from .plan import (TIME_EPS, Claim, HumanObservation, Plan, StepRef, TaskType, Violation, WorldMap, claims,
                   stop_claims)


# ---------------------------------------------------------------------------
# S-1: Capacity violation detection (sweep-line algorithm)
# ---------------------------------------------------------------------------

class CapacityConflict(NamedTuple):
    """A detected capacity conflict between claims."""
    resource_id: str
    claims: tuple[Claim, ...]
    t_start: float
    t_end: float
    is_edge_swap: bool
    capacity: int  # of resource_id; R-1 needs it to know how many robots must yield


class OverCapacity(NamedTuple):
    """A stretch of time in which more distinct robots hold claims on one resource than it allows."""
    claims: tuple[Claim, ...]  # every claim taking part, sorted by robot, step, start, end
    t_start: float
    t_end: float


def sweep_over_capacity(claims: Iterable[Claim], capacity: int) -> list[OverCapacity]:
    """Sweep line over the claims on ONE resource: every stretch of time in which more
    than `capacity` distinct robots hold a claim, in time order.

    Shared by S-1 (a region or resource) and S-2 (the surroundings of a person).

    Robots are counted, not claims. A robot is present from the start of a claim until the
    end of the last of its claims that overlap or touch (to TIME_EPS): its consecutive steps
    overlap with margin > 0 and touch with margin = 0, and either way it is one robot,
    there throughout, so a stretch is not cut in two at its handover. Between robots,
    intervals are half-open: one leaving at t and another arriving at t do not overlap
    (ends are processed before starts). A stretch lasts until the count is back within
    capacity, so overlaps that chain (r1 with r2, r2 with r3) are one stretch with all
    three robots (see assign_yield). A stretch's claims are every claim overlapping it.
    """
    claims = list(claims)
    presence: list[tuple[float, float]] = []      # each robot's stretches of being there
    by_robot: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_robot[c.robot_id].append(c)
    for robot_claims in by_robot.values():
        robot_claims.sort(key=lambda c: (c.start, c.end))
        start, end = robot_claims[0].start, robot_claims[0].end
        for c in robot_claims[1:]:
            if c.start <= end + TIME_EPS:
                end = max(end, c.end)
            else:
                presence.append((start, end))
                start, end = c.start, c.end
        presence.append((start, end))

    events = sorted([(end, False) for _, end in presence] + [(start, True) for start, _ in presence])
    out: list[OverCapacity] = []
    present = 0
    stretch_start: float | None = None
    for time, is_start in events:
        if is_start:
            present += 1
            if present > capacity and stretch_start is None:
                stretch_start = time
        else:
            present -= 1
            if stretch_start is not None and present <= capacity:
                taking_part = sorted((c for c in claims if c.start < time and c.end > stretch_start),
                                     key=lambda c: (c.robot_id, c.step_index, c.start, c.end))
                out.append(OverCapacity(tuple(taking_part), stretch_start, time))
                stretch_start = None
    return out


def _capacity_of(resource_id: str, world: WorldMap) -> int:
    # Unknown ids are rejected by validate_plan; default to the strictest value.
    return world.capacity(resource_id) if world.knows(resource_id) else 1


def _detect_edge_swap(
    all_conflicts: list[tuple[str, tuple[Claim, ...], float, float]],
    world: WorldMap,
) -> set[int]:
    """Detect which conflicts are edge swaps.

    An edge swap occurs when two robots are swapping adjacent regions head-on:
    - Robot A is moving from region X to region Y
    - Robot B is moving from region Y to region X
    - Both movements happen at overlapping times

    With margin > 0, this surfaces as capacity violations in BOTH regions
    at approximately the same time.

    Returns:
        Set of indices into all_conflicts that are edge swaps
    """
    swap_indices: set[int] = set()

    # Group conflicts by the pair of robots involved
    from itertools import combinations

    for i, (res_i, claims_i, t_start_i, t_end_i) in enumerate(all_conflicts):
        if res_i not in world.regions:
            continue

        robot_ids_i = frozenset(c.robot_id for c in claims_i)
        if len(robot_ids_i) != 2:
            continue

        # Look for another conflict in an adjacent region with the same robots
        region_i = world.regions[res_i]
        for j, (res_j, claims_j, t_start_j, t_end_j) in enumerate(all_conflicts):
            if i >= j:  # Avoid duplicate checking
                continue
            if res_j not in world.regions:
                continue
            if res_j not in region_i.neighbors:
                continue

            robot_ids_j = frozenset(c.robot_id for c in claims_j)
            if robot_ids_i != robot_ids_j:
                continue

            # Check if time intervals overlap (indicating simultaneous swap)
            if t_start_i < t_end_j and t_start_j < t_end_i:
                swap_indices.add(i)
                swap_indices.add(j)

    return swap_indices


def check_capacity_violations(
    plans: list[Plan],
    world: WorldMap,
    margin: float = 0.0,
    hold_final: bool = True,
) -> list[CapacityConflict]:
    """Detect all capacity violations across multiple plans using sweep-line.

    Args:
        plans: List of plans to check for conflicts
        world: World map with region capacities
        margin: Widen each claim interval by this many seconds (for swap detection)
        hold_final: Whether robots hold their final region to +inf

    Returns:
        List of CapacityConflict describing each violation
    """
    # Collect all claims grouped by resource
    resource_claims: dict[str, list[Claim]] = defaultdict(list)
    for plan in plans:
        for claim in claims(plan, margin=margin, hold_final=hold_final):
            resource_claims[claim.resource_id].append(claim)

    # First pass: detect all capacity violations without edge swap labeling,
    # each resource on its own
    raw_conflicts: list[tuple[str, tuple[Claim, ...], float, float]] = []
    for resource_id, resource_claims_list in resource_claims.items():
        for s in sweep_over_capacity(resource_claims_list, _capacity_of(resource_id, world)):
            raw_conflicts.append((resource_id, s.claims, s.t_start, s.t_end))

    # Second pass: detect edge swaps by looking at pairs of conflicts
    swap_indices = _detect_edge_swap(raw_conflicts, world)

    # Build final conflicts with edge swap labels
    conflicts: list[CapacityConflict] = []
    for i, (resource_id, conflict_claims, t_start, t_end) in enumerate(raw_conflicts):
        conflicts.append(CapacityConflict(
            resource_id=resource_id,
            claims=conflict_claims,
            t_start=t_start,
            t_end=t_end,
            is_edge_swap=(i in swap_indices),
            capacity=_capacity_of(resource_id, world),
        ))

    return conflicts


def capacity_conflicts_to_violations(
    conflicts: list[CapacityConflict],
) -> list[Violation]:
    """Convert CapacityConflict objects to Violation objects for reporting."""
    violations: list[Violation] = []

    for conflict in conflicts:
        robot_ids = sorted(set(c.robot_id for c in conflict.claims))
        refs = tuple(
            StepRef(robot_id=c.robot_id, step_index=c.step_index)
            for c in conflict.claims
        )

        if conflict.is_edge_swap:
            code = "EDGE_SWAP"
            message = (
                f"robots {', '.join(robot_ids)} attempt head-on swap in "
                f"{conflict.resource_id} at t=[{conflict.t_start:.2f}, {conflict.t_end:.2f})"
            )
        else:
            code = "CAPACITY_EXCEEDED"
            message = (
                f"region {conflict.resource_id} has {len(robot_ids)} robots "
                f"but capacity is exceeded at t=[{conflict.t_start:.2f}, {conflict.t_end:.2f})"
            )

        violations.append(Violation(code=code, message=message, refs=refs,
                                    resource_id=conflict.resource_id))

    return violations


# ---------------------------------------------------------------------------
# S-2: Human crowding (S-1's sweep, over each person's surroundings)
# ---------------------------------------------------------------------------

def check_crowding(
    plans: list[Plan],
    people: Sequence[HumanObservation],
    limit: CrowdingLimit,
    margin: float = 0.0,
    hold_final: bool = True,
) -> list[CapacityConflict]:
    """Detect every stretch of time in which more than limit.max_robots robots are
    within limit.radius metres of the same person.

    Each person's surroundings are swept like one of S-1's regions, with capacity N;
    a conflict's resource_id is the person's id. They are never edge swaps.
    """
    ids = [p.human_id for p in people]
    shared = sorted({i for i in ids if ids.count(i) > 1})
    if shared:
        raise ValueError(f"two people have the id {shared[0]!r}; every person needs an id of their own")
    conflicts: list[CapacityConflict] = []
    for person in people:
        person_claims = [c for plan in plans
                         for c in crowding_claims(plan, [person], limit.radius, margin, hold_final)]
        for s in sweep_over_capacity(person_claims, limit.max_robots):
            conflicts.append(CapacityConflict(person.human_id, s.claims, s.t_start, s.t_end,
                                              is_edge_swap=False, capacity=limit.max_robots))
    return conflicts


def crowding_conflicts_to_violations(conflicts: list[CapacityConflict],
                                     limit: CrowdingLimit) -> list[Violation]:
    """HUMAN_CROWDING violations. resource_id is the person; each step is referred to once,
    even when the robot was near the person twice during it."""
    most = "1 robot" if limit.max_robots == 1 else f"{limit.max_robots} robots"
    violations: list[Violation] = []
    for conflict in conflicts:
        robot_ids = sorted({c.robot_id for c in conflict.claims})
        refs = tuple(dict.fromkeys(StepRef(robot_id=c.robot_id, step_index=c.step_index)
                                   for c in conflict.claims))
        violations.append(Violation(
            code="HUMAN_CROWDING",
            message=(f"robots {', '.join(robot_ids)} are within {limit.radius:g} m of "
                     f"{conflict.resource_id} at t=[{conflict.t_start:.2f}, {conflict.t_end:.2f}); "
                     f"at most {most} may be"),
            refs=refs,
            resource_id=conflict.resource_id,
        ))
    return violations


# ---------------------------------------------------------------------------
# S-4: Node occupancy (S-1's sweep, over each node where robots stop)
# ---------------------------------------------------------------------------

def check_node_occupancy(
    plans: list[Plan],
    world: WorldMap,
    margin: float = 0.0,
    hold_final: bool = True,
) -> list[CapacityConflict]:
    """Detect every stretch of time in which two robots stand still at the same node.

    Each node is swept like one of S-1's regions, with capacity 1; a conflict's
    resource_id is the node. Stops in a zone of capacity 1 are left out: S-1 already
    allows only one robot in the whole zone, stopped or not, and one problem should
    be reported once. Driving through a node where another robot stands is not checked.
    """
    by_node: dict[str, list[Claim]] = defaultdict(list)
    for plan in plans:
        for c in stop_claims(plan, margin=margin, hold_final=hold_final):
            region = plan.steps[c.step_index].region
            if world.knows(region) and world.capacity(region) == 1:
                continue
            by_node[c.resource_id].append(c)
    conflicts: list[CapacityConflict] = []
    for node, node_claims in by_node.items():
        for s in sweep_over_capacity(node_claims, 1):
            conflicts.append(CapacityConflict(node, s.claims, s.t_start, s.t_end,
                                              is_edge_swap=False, capacity=1))
    return conflicts


def node_conflicts_to_violations(conflicts: list[CapacityConflict]) -> list[Violation]:
    """NODE_OCCUPIED violations. resource_id is the node; each step is referred to once."""
    violations: list[Violation] = []
    for conflict in conflicts:
        robot_ids = sorted({c.robot_id for c in conflict.claims})
        refs = tuple(dict.fromkeys(StepRef(robot_id=c.robot_id, step_index=c.step_index)
                                   for c in conflict.claims))
        violations.append(Violation(
            code="NODE_OCCUPIED",
            message=(f"robots {', '.join(robot_ids)} would stop at {conflict.resource_id} at once, "
                     f"at t=[{conflict.t_start:.2f}, {conflict.t_end:.2f})"),
            refs=refs,
            resource_id=conflict.resource_id,
        ))
    return violations


# ---------------------------------------------------------------------------
# R-1: Yield assignment (who should replan when a conflict is detected)
# ---------------------------------------------------------------------------

@dataclass
class YieldRecord:
    """Record of a single yield event."""
    conflict_id: str
    timestamp: float
    opponent_robot_id: str


@dataclass
class FairnessLedger:
    """Two counts per robot, written at different times (rules_v1.md, "Updating the ledger"):

      yields                  conflicts the robot gave way in; R-1 and F-1 use them.
                              Recorded when the conflict is resolved (the plan is approved),
                              not on each REJECT: otherwise R-1 would swap the robots on every
                              round in which the planner fails to fix the conflict.
      consecutive_rejections  REJECTs in a row that told the robot to yield; F-2 uses them.
                              Reset when the robot's plan is approved.
    """
    yields: dict[str, list[YieldRecord]] = field(default_factory=lambda: defaultdict(list))
    consecutive_rejections: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    starvation_threshold: int = 3   # F-2's M

    def __post_init__(self):
        if self.starvation_threshold < 1:
            raise ValueError(f"starvation_threshold must be at least 1, got {self.starvation_threshold}")

    def yield_count(self, robot_id: str) -> int:
        """Get the total number of times this robot has yielded."""
        return len(self.yields[robot_id])

    def record_yield(self, robot_id: str, conflict_id: str, timestamp: float, opponent_id: str):
        """Record that a robot gave way in a conflict that has been resolved."""
        self.yields[robot_id].append(YieldRecord(conflict_id, timestamp, opponent_id))

    def record_rejection(self, robot_id: str):
        """Record that a REJECT told this robot to yield (counts toward F-2)."""
        self.consecutive_rejections[robot_id] += 1

    def record_acceptance(self, robot_id: str):
        """Record that a robot's plan was accepted (reset consecutive rejections)."""
        self.consecutive_rejections[robot_id] = 0

    def is_starving(self, robot_id: str) -> bool:
        """Check if a robot has been rejected too many times consecutively (F-2)."""
        return self.consecutive_rejections[robot_id] >= self.starvation_threshold


# Task type priority ranking (lower number = higher priority)
TASK_PRIORITY: dict[TaskType, int] = {
    TaskType.DELIVERY: 1,
    TaskType.SERVICE: 2,
}


def first_entry_times(conflicts: Iterable[CapacityConflict]) -> dict[str, float]:
    """robot_id -> the earliest start of its claims in any of these conflicts.

    R-1 compares robots by this one value per round, not by when they enter
    each conflict's region: that gives every robot one global sort key, so the
    yield relations of a round cannot form a cycle. Taken per conflict, the two
    halves of a head-on swap would each send the other robot back (each robot
    is first in the region it starts in), and the orderings would contradict.
    """
    entry: dict[str, float] = {}
    for conflict in conflicts:
        for c in conflict.claims:
            entry[c.robot_id] = min(c.start, entry.get(c.robot_id, math.inf))
    return entry


def assign_yield(
    conflict: CapacityConflict,
    plans: dict[str, Plan],
    ledger: FairnessLedger,
    entry_times: Mapping[str, float] | None = None,
) -> list[str]:
    """Decide which robots should yield (replan) given a conflict.

    With n distinct robots in a region of capacity c, n - c robots yield, so
    three robots in a capacity-1 aisle send two of them back to replan.
    Robots are ranked by `_yield_key` (F-2, then R-1) and the first n - c
    yield; the remaining c keep their plans.

    entry_times: first_entry_times over every conflict of the round;
    check_cross_plan_conflicts passes it. If None, only this conflict is used.

    Known over-yield: n counts every robot in the conflict window, but they
    may not all be inside at once. In a chain where r1 overlaps r2 and r2
    overlaps r3 but r1 and r3 never meet, capacity 1 asks two robots to
    yield when removing r2 alone would do. This is conservative: the robots
    kept never exceed capacity, so the conflict is always resolved, at the
    cost of an extra replan. Finding the minimum set is an interval
    selection problem; revisit if experiments show chains are common.

    Args:
        conflict: The detected capacity conflict
        plans: Dict mapping robot_id to their Plan
        ledger: FairnessLedger tracking cumulative yields and starvation

    Returns:
        robot_ids that should yield, in yield order (empty if no conflict)
    """
    robot_ids = sorted(set(c.robot_id for c in conflict.claims))
    n_yield = max(len(robot_ids) - conflict.capacity, 0)
    if entry_times is None:
        entry_times = first_entry_times([conflict])

    ranked = sorted(robot_ids, key=lambda r: _yield_key(r, plans, ledger, entry_times))
    return ranked[:n_yield]


def _yield_key(robot_id: str, plans: dict[str, Plan], ledger: FairnessLedger,
               entry_times: Mapping[str, float]) -> tuple[bool, int, int, float, str]:
    """Sort key for R-1 and F-2 together: lower key = should yield (replan).

    1. Starving robots (F-2) sort last, so they yield only when there are
       more starving robots than capacity; among themselves, R-1 decides.
    2. Fewer cumulative yields = should yield this time (R-1).
    3. Higher task_priority number (SERVICE=2) = lower priority = should yield.
    4. Later first entry into a conflict region = should yield: the robot that
       gets there first keeps going. Rounded to 1e-6 s so float noise does not decide.
    5. robot_id, so the order is total.

    One key for both rules keeps the starvation branch from ranking the
    non-starving robots in a different direction than R-1.
    """
    task_priority = TASK_PRIORITY.get(plans[robot_id].task_type, 99)
    entry = round(entry_times[robot_id], 6)
    return (ledger.is_starving(robot_id), ledger.yield_count(robot_id), -task_priority,
            -entry, robot_id)


# ---------------------------------------------------------------------------
# Main check function combining S-1, S-2, S-4 and R-1
# ---------------------------------------------------------------------------

def check_cross_plan_conflicts(
    plans: list[Plan],
    world: WorldMap,
    ledger: FairnessLedger | None = None,
    margin: float = 0.5,
    hold_final: bool = True,
    people: Sequence[HumanObservation] = (),
    crowding: CrowdingLimit | None = None,
) -> tuple[list[Violation], dict[str, list[Violation]]]:
    """Check multiple plans for cross-plan conflicts and assign yields.

    S-4 runs on the stops the plans declare (PlanStep.end_node, from the compiler).
    Every conflict of the round, S-1, S-2 and S-4 alike, ranks robots by the same
    entry times (first_entry_times over all of them). One sort key per robot keeps
    the yield relations of a round free of cycles across the rules as well: ranked
    per rule, S-1 could have b wait for a while S-2 or S-4 has a wait for b.

    Args:
        plans: List of plans to check
        world: World map
        ledger: FairnessLedger for yield tracking (optional)
        margin: Time margin for swap detection and timing error (S-1 and S-2)
        hold_final: Whether robots hold final position
        people: People to check S-2 around; none, no S-2
        crowding: S-2's R and N; needed when there are people

    Returns:
        Tuple of:
        - List of all violations detected: S-1's, S-2's, then S-4's
        - Dict mapping robot_id to violations they are responsible for (should replan)
    """
    if ledger is None:
        ledger = FairnessLedger()
    if people and crowding is None:
        raise ValueError("people were given without a CrowdingLimit: S-2 needs R and N")

    plans_dict = {p.robot_id: p for p in plans}

    # Detect capacity violations (S-1) and crowding around people (S-2)
    conflicts = check_capacity_violations(plans, world, margin=margin, hold_final=hold_final)
    all_violations = capacity_conflicts_to_violations(conflicts)
    if people:
        crowded = check_crowding(plans, people, crowding, margin=margin, hold_final=hold_final)
        conflicts += crowded
        all_violations += crowding_conflicts_to_violations(crowded, crowding)
    stopped = check_node_occupancy(plans, world, margin=margin, hold_final=hold_final)
    conflicts += stopped
    all_violations += node_conflicts_to_violations(stopped)

    # Assign yields (R-1), every conflict ranking robots by the same entry times
    robot_violations: dict[str, list[Violation]] = defaultdict(list)
    entry_times = first_entry_times(conflicts)

    for conflict, violation in zip(conflicts, all_violations):
        for yielding_robot in assign_yield(conflict, plans_dict, ledger, entry_times):
            robot_violations[yielding_robot].append(violation)

    return all_violations, dict(robot_violations)
