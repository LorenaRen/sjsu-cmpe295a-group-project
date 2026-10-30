"""check_team_plan: the shared checker run on a Module 1 TeamPlan.

  TeamPlan + mrs_sim map + where each robot is
    -> compile (compiler.py)      unknown robot or target, no route, deadlock: stop here
    -> each robot: P-1, P-2, P-3  (policy.validate_policy)
    -> all robots: S-1 + R-1      (cross_plan.check_cross_plan_conflicts)

A policy violation is for the robot that proposed the step to fix. For a
capacity conflict, R-1 and the fairness ledger choose which robots replan;
TeamPlan priority plays no part. The ledger is only read here: recording yields
belongs to whatever runs the replan loop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, NamedTuple

from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan

from ..cross_plan import FairnessLedger, check_cross_plan_conflicts
from ..plan import RegionKind, TaskType, Violation
from ..policy import DEFAULT_MAX_DURATION, AccessPolicy, validate_policy
from .compiler import CompiledTeamPlan, compile_team_plan
from .sim_map import SimMap

# The map only labels a zone "restricted"; this is the rule that goes with the
# label unless the caller passes another policy: no robot may enter.
NO_ENTRY_TO_RESTRICTED = AccessPolicy(by_kind={RegionKind.RESTRICTED: frozenset()})


class Finding(NamedTuple):
    violation: Violation
    reason: str                # mrs_common.reasons code, e.g. "capacity_exceeded"
    task_ids: tuple[str, ...]  # TeamPlan tasks that the violation's steps came from


@dataclass(frozen=True)
class TeamCheckResult:
    compiled: CompiledTeamPlan
    findings: tuple[Finding, ...]
    # robot_id -> what it must fix. Empty when the plan does not compile: then
    # the TeamPlan as a whole goes back to the planner.
    must_replan: dict[str, tuple[Violation, ...]]

    @property
    def approved(self) -> bool:
        return not self.findings


def reason_of(violation: Violation) -> str:
    """The mrs_common.reasons code for a checker violation (its code in lower case).
    Raises ValueError if reasons.py does not list it, so a new checker code cannot
    reach the logs unregistered."""
    return R.check(violation.code.lower())


def check_team_plan(
    team_plan: TeamPlan,
    sim_map: SimMap,
    start_nodes: Mapping[str, str],
    *,
    speed: float,
    t0: float = 0.0,
    ledger: FairnessLedger | None = None,
    access_policy: AccessPolicy = NO_ENTRY_TO_RESTRICTED,
    margin: float = 0.5,
    max_duration: float = DEFAULT_MAX_DURATION,
    task_types: Mapping[str, TaskType] | None = None,
    plan_id: str = "teamplan",
    revision: int = 0,
) -> TeamCheckResult:
    """Compile a TeamPlan and run the policy and cross-plan checks on it.

    speed, t0, task_types, plan_id and revision go to compile_team_plan;
    margin to the S-1 check; access_policy and max_duration to P-2 and P-3.
    """
    compiled = compile_team_plan(team_plan, sim_map, start_nodes, speed=speed, t0=t0,
                                 task_types=task_types, plan_id=plan_id, revision=revision)
    if compiled.violations:
        findings = tuple(_finding(v, compiled) for v in compiled.violations)
        return TeamCheckResult(compiled, findings, {})

    violations: list[Violation] = []
    must_replan: dict[str, list[Violation]] = {}
    for robot, plan in compiled.plans.items():
        found = validate_policy(plan, sim_map.world, access_policy, max_duration)
        violations.extend(found)
        if found:
            must_replan.setdefault(robot, []).extend(found)

    conflicts, yielders = check_cross_plan_conflicts(
        list(compiled.plans.values()), sim_map.world, ledger, margin=margin)
    violations.extend(conflicts)
    for robot, found in yielders.items():
        must_replan.setdefault(robot, []).extend(found)

    return TeamCheckResult(
        compiled,
        tuple(_finding(v, compiled) for v in violations),
        {robot: tuple(found) for robot, found in sorted(must_replan.items())},
    )


def _finding(violation: Violation, compiled: CompiledTeamPlan) -> Finding:
    task_ids: list[str] = []
    for ref in violation.refs:
        task_id = compiled.origins[ref.robot_id][ref.step_index].task_id
        if task_id is not None and task_id not in task_ids:
            task_ids.append(task_id)
    return Finding(violation, reason_of(violation), tuple(task_ids))
