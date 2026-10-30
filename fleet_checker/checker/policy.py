"""Policy checks for single-plan validation.

P-1: Final stop in passthrough zone
    A plan whose last step is in an AISLE, DOORWAY or KEEP_CLEAR region. With
    hold_final=True the robot claims that region to +inf, blocking other traffic
    indefinitely.

P-2: Region access restriction
    Certain task_type values are forbidden from entering certain regions,
    configured per region id and per region kind in an AccessPolicy.

P-3: Maximum plan duration
    A plan spanning more than T_max seconds is rejected.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .plan import Plan, RegionKind, StepRef, TaskType, Violation, WorldMap


# ---------------------------------------------------------------------------
# P-1: Final stop in passthrough zone
# ---------------------------------------------------------------------------

PASSTHROUGH_KINDS: frozenset[RegionKind] = frozenset({
    RegionKind.AISLE,
    RegionKind.DOORWAY,
    RegionKind.KEEP_CLEAR,
})


def check_final_stop_in_passthrough(
    plan: Plan,
    world: WorldMap,
) -> list[Violation]:
    """P-1: Check if plan ends in a passthrough zone (AISLE, DOORWAY or KEEP_CLEAR).

    A robot that finishes in an aisle, doorway or keep-clear zone and holds
    that position will block other traffic indefinitely.
    """
    final_step = plan.steps[-1]
    final_region_id = final_step.region

    if final_region_id not in world.regions:
        return []

    final_region = world.regions[final_region_id]

    if final_region.kind in PASSTHROUGH_KINDS:
        return [Violation(
            code="FINAL_STOP_IN_PASSTHROUGH",
            message=f"plan ends in {final_region.kind.value} region '{final_region_id}' "
                    f"which will block traffic when held",
            refs=(StepRef(robot_id=plan.robot_id, step_index=len(plan.steps) - 1),),
        )]

    return []


# ---------------------------------------------------------------------------
# P-2: Region access restriction
# ---------------------------------------------------------------------------

class AccessPolicy(BaseModel):
    """Which task types may enter a region, by region id and by region kind.

    Two tables instead of one dict keyed by either: narrow_aisle names its
    narrow zone "aisle", which is also RegionKind.AISLE's value, so a single
    key could not say whether it meant that one region or every aisle.

    A region missing from a table is not restricted by that table. A step
    must pass both tables; a by_id entry cannot grant what by_kind denies.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    by_id: dict[str, frozenset[TaskType]] = Field(default_factory=dict)
    by_kind: dict[RegionKind, frozenset[TaskType]] = Field(default_factory=dict)


DEFAULT_ACCESS_POLICY = AccessPolicy()


def check_region_access(
    plan: Plan,
    world: WorldMap,
    policy: AccessPolicy | None = None,
) -> list[Violation]:
    """P-2: Check if plan violates region access restrictions.

    Args:
        plan: The plan to check
        world: World map with region definitions
        policy: Allowed TaskTypes per region id and per region kind.
                If None, uses DEFAULT_ACCESS_POLICY (allow all).
    """
    if policy is None:
        policy = DEFAULT_ACCESS_POLICY

    violations: list[Violation] = []

    for i, step in enumerate(plan.steps):
        region_id = step.region

        if region_id not in world.regions:
            continue

        region = world.regions[region_id]

        # Both tables must allow; report the region_id rule first
        allowed_by_id = policy.by_id.get(region_id)
        allowed_by_kind = policy.by_kind.get(region.kind)

        if allowed_by_id is not None and plan.task_type not in allowed_by_id:
            violations.append(Violation(
                code="REGION_ACCESS_DENIED",
                message=f"task_type '{plan.task_type.value}' cannot enter region '{region_id}'",
                refs=(StepRef(robot_id=plan.robot_id, step_index=i),),
            ))
        elif allowed_by_kind is not None and plan.task_type not in allowed_by_kind:
            violations.append(Violation(
                code="REGION_ACCESS_DENIED",
                message=f"task_type '{plan.task_type.value}' cannot enter "
                        f"{region.kind.value} regions (at '{region_id}')",
                refs=(StepRef(robot_id=plan.robot_id, step_index=i),),
            ))

    return violations


# ---------------------------------------------------------------------------
# P-3: Maximum plan duration
# ---------------------------------------------------------------------------

DEFAULT_MAX_DURATION: float = 3600.0  # 1 hour


def check_max_duration(
    plan: Plan,
    max_duration: float = DEFAULT_MAX_DURATION,
) -> list[Violation]:
    """P-3: Check if plan exceeds maximum allowed duration."""
    duration = plan.t_end - plan.t_start

    if duration > max_duration:
        return [Violation(
            code="MAX_DURATION_EXCEEDED",
            message=f"plan duration {duration:.1f}s exceeds maximum {max_duration:.1f}s",
            refs=(
                StepRef(robot_id=plan.robot_id, step_index=0),
                StepRef(robot_id=plan.robot_id, step_index=len(plan.steps) - 1),
            ),
        )]

    return []


# ---------------------------------------------------------------------------
# Combined policy check
# ---------------------------------------------------------------------------

def validate_policy(
    plan: Plan,
    world: WorldMap,
    access_policy: AccessPolicy | None = None,
    max_duration: float = DEFAULT_MAX_DURATION,
) -> list[Violation]:
    """Run all policy checks on a single plan."""
    violations: list[Violation] = []
    violations.extend(check_final_stop_in_passthrough(plan, world))
    violations.extend(check_region_access(plan, world, access_policy))
    violations.extend(check_max_duration(plan, max_duration))
    return violations
