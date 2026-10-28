"""Cross-plan conflict detection for the shared checking layer.

S-1: Interval capacity violation
    Same region or resource occupied by more robots than its `capacity` allows
    during any overlapping time window.

    When two robots swap adjacent regions head-on (A→B while B→A), the detector
    labels it `EDGE_SWAP` instead of `CAPACITY_EXCEEDED`.

R-1: Yield assignment
    When a conflict is detected, decide which robot yields based on:
    1. Fewer cumulative yields (from FairnessLedger) yields this time
    2. Tie-break: task_type rank — SERVICE yields to DELIVERY
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterator, NamedTuple

from .plan import Claim, Plan, StepRef, TaskType, Violation, WorldMap, claims


# ---------------------------------------------------------------------------
# S-1: Capacity violation detection (sweep-line algorithm)
# ---------------------------------------------------------------------------

class ConflictEvent(NamedTuple):
    """An event in the sweep-line algorithm."""
    time: float
    is_start: bool  # True = claim starts, False = claim ends
    claim: Claim


class CapacityConflict(NamedTuple):
    """A detected capacity conflict between claims."""
    resource_id: str
    claims: tuple[Claim, ...]
    t_start: float
    t_end: float
    is_edge_swap: bool


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

    # First pass: detect all capacity violations without edge swap labeling
    raw_conflicts: list[tuple[str, tuple[Claim, ...], float, float]] = []

    # Check each resource independently
    for resource_id, resource_claims_list in resource_claims.items():
        if len(resource_claims_list) < 2:
            continue

        capacity = world.capacity(resource_id) if world.knows(resource_id) else 1

        # Build sweep-line events
        events: list[ConflictEvent] = []
        for claim in resource_claims_list:
            events.append(ConflictEvent(claim.start, True, claim))
            events.append(ConflictEvent(claim.end, False, claim))

        # Sort: by time, then ends before starts at same time (half-open intervals)
        events.sort(key=lambda e: (e.time, e.is_start))

        # Sweep through events
        active_claims: set[Claim] = set()
        conflict_start: float | None = None
        conflict_claims: set[Claim] = set()

        for event in events:
            if event.is_start:
                active_claims.add(event.claim)

                if len(active_claims) > capacity:
                    # Entering a conflict state
                    if conflict_start is None:
                        conflict_start = event.time
                    conflict_claims.update(active_claims)
            else:
                active_claims.discard(event.claim)

                if conflict_start is not None and len(active_claims) <= capacity:
                    # Exiting conflict state - record the conflict
                    conflict_tuple = tuple(sorted(conflict_claims, key=lambda c: (c.robot_id, c.step_index)))
                    raw_conflicts.append((resource_id, conflict_tuple, conflict_start, event.time))

                    conflict_start = None
                    conflict_claims.clear()

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
                f"region {conflict.resource_id} has {len(conflict.claims)} robots "
                f"but capacity is exceeded at t=[{conflict.t_start:.2f}, {conflict.t_end:.2f})"
            )

        violations.append(Violation(code=code, message=message, refs=refs))

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
    """Tracks cumulative yields per robot for fair conflict resolution."""
    yields: dict[str, list[YieldRecord]] = field(default_factory=lambda: defaultdict(list))
    consecutive_rejections: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def yield_count(self, robot_id: str) -> int:
        """Get the total number of times this robot has yielded."""
        return len(self.yields[robot_id])

    def record_yield(self, robot_id: str, conflict_id: str, timestamp: float, opponent_id: str):
        """Record that a robot yielded in a conflict."""
        self.yields[robot_id].append(YieldRecord(conflict_id, timestamp, opponent_id))
        # Reset consecutive rejections for the yielding robot
        self.consecutive_rejections[robot_id] += 1

    def record_acceptance(self, robot_id: str):
        """Record that a robot's plan was accepted (reset consecutive rejections)."""
        self.consecutive_rejections[robot_id] = 0

    def is_starving(self, robot_id: str, threshold: int = 3) -> bool:
        """Check if a robot has been rejected too many times consecutively (F-2)."""
        return self.consecutive_rejections[robot_id] >= threshold


# Task type priority ranking (lower number = higher priority)
TASK_PRIORITY: dict[TaskType, int] = {
    TaskType.DELIVERY: 1,
    TaskType.SERVICE: 2,
}


def assign_yield(
    conflict: CapacityConflict,
    plans: dict[str, Plan],
    ledger: FairnessLedger,
) -> str:
    """Decide which robot should yield (replan) given a conflict.

    Resolution order (R-1):
    1. The robot with fewer cumulative yields yields this time
    2. Tie-break: SERVICE yields to DELIVERY (lower task_type priority yields)

    Args:
        conflict: The detected capacity conflict
        plans: Dict mapping robot_id to their Plan
        ledger: FairnessLedger tracking cumulative yields

    Returns:
        robot_id of the robot that should yield
    """
    robot_ids = sorted(set(c.robot_id for c in conflict.claims))

    if len(robot_ids) < 2:
        # No conflict if only one robot
        return robot_ids[0] if robot_ids else ""

    # Check for starvation override (F-2)
    starving_robots = [rid for rid in robot_ids if ledger.is_starving(rid)]
    if len(starving_robots) == 1:
        # The starving robot gets priority, others yield
        non_starving = [rid for rid in robot_ids if rid not in starving_robots]
        if non_starving:
            # Pick the one with most yields to yield again (they can handle it)
            return max(non_starving, key=lambda r: (
                ledger.yield_count(r),
                TASK_PRIORITY.get(plans[r].task_type, 99)
            ))

    # Normal R-1 resolution
    # "The robot with fewer cumulative yields yields this time"
    def yield_priority(robot_id: str) -> tuple[int, int]:
        """Lower tuple = should yield (be asked to replan)."""
        yield_count = ledger.yield_count(robot_id)
        task_priority = TASK_PRIORITY.get(plans[robot_id].task_type, 99)
        # Fewer yields = should yield this time
        # Higher task_priority number (SERVICE=2) = lower priority = should yield
        return (yield_count, -task_priority)

    # Robot with lowest yield_priority should yield
    return min(robot_ids, key=yield_priority)


# ---------------------------------------------------------------------------
# Main check function combining S-1 and R-1
# ---------------------------------------------------------------------------

def check_cross_plan_conflicts(
    plans: list[Plan],
    world: WorldMap,
    ledger: FairnessLedger | None = None,
    margin: float = 0.5,
    hold_final: bool = True,
) -> tuple[list[Violation], dict[str, list[Violation]]]:
    """Check multiple plans for cross-plan conflicts and assign yields.

    Args:
        plans: List of plans to check
        world: World map
        ledger: FairnessLedger for yield tracking (optional)
        margin: Time margin for swap detection
        hold_final: Whether robots hold final position

    Returns:
        Tuple of:
        - List of all violations detected
        - Dict mapping robot_id to violations they are responsible for (should replan)
    """
    if ledger is None:
        ledger = FairnessLedger()

    plans_dict = {p.robot_id: p for p in plans}

    # Detect capacity violations (S-1)
    conflicts = check_capacity_violations(plans, world, margin=margin, hold_final=hold_final)
    all_violations = capacity_conflicts_to_violations(conflicts)

    # Assign yields (R-1)
    robot_violations: dict[str, list[Violation]] = defaultdict(list)

    for conflict, violation in zip(conflicts, all_violations):
        yielding_robot = assign_yield(conflict, plans_dict, ledger)
        robot_violations[yielding_robot].append(violation)

    return all_violations, dict(robot_violations)
