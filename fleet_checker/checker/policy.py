"""Policy checks for single-plan validation.

P-1: Final stop in passthrough zone
    A plan whose last step is in an AISLE or DOORWAY region. With hold_final=True
    the robot claims that region to +inf, blocking other traffic indefinitely.

P-2: Region access restriction
    Certain task_type values are forbidden from entering certain regions.

P-3: Maximum plan duration
    A plan spanning more than T_max seconds is rejected.
"""
from __future__ import annotations

from .plan import Plan, RegionKind, StepRef, TaskType, Violation, WorldMap


# ---------------------------------------------------------------------------
# P-1: Final stop in passthrough zone
# ---------------------------------------------------------------------------

PASSTHROUGH_KINDS: frozenset[RegionKind] = frozenset({
    RegionKind.AISLE,
    RegionKind.DOORWAY,
})


def check_final_stop_in_passthrough(
    plan: Plan,
    world: WorldMap,
) -> list[Violation]:
    """P-1: Check if plan ends in a passthrough zone (AISLE or DOORWAY).

    A robot that finishes in an aisle or doorway and holds that position
    will block other traffic indefinitely.
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

# Access policy: maps region_id or region_kind -> set of allowed TaskTypes
AccessPolicy = dict[str, frozenset[TaskType]]

DEFAULT_ACCESS_POLICY: AccessPolicy = {}


def check_region_access(
    plan: Plan,
    world: WorldMap,
    policy: AccessPolicy | None = None,
) -> list[Violation]:
    """P-2: Check if plan violates region access restrictions.

    Args:
        plan: The plan to check
        world: World map with region definitions
        policy: Access policy mapping region_id or region_kind to allowed TaskTypes.
                If None, uses DEFAULT_ACCESS_POLICY (allow all).
    """
    if policy is None:
        policy = DEFAULT_ACCESS_POLICY

    if not policy:
        return []

    violations: list[Violation] = []

    for i, step in enumerate(plan.steps):
        region_id = step.region

        if region_id not in world.regions:
            continue

        region = world.regions[region_id]

        # Check by region_id first, then by region_kind
        allowed_by_id = policy.get(region_id)
        allowed_by_kind = policy.get(region.kind.value)

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
