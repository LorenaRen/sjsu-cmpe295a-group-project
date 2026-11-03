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


@dataclass(frozen=True)
class CheckSettings:
    """What check_team_plan was called with, besides the plan, map, start nodes and ledger."""
    speed: float
    t0: float
    margin: float
    max_duration: float
    access_policy: AccessPolicy
    task_types: Mapping[str, TaskType] | None
    plan_id: str
    revision: int


class Finding(NamedTuple):
    violation: Violation
    reason: str                # mrs_common.reasons code, e.g. "capacity_exceeded"
    task_ids: tuple[str, ...]  # TeamPlan tasks that the violation's steps came from
    must_fix: tuple[str, ...]  # robots that have to change their plan; () if it does not compile


@dataclass(frozen=True)
class TeamCheckResult:
    compiled: CompiledTeamPlan
    findings: tuple[Finding, ...]
    # robot_id -> what it must fix. Empty when the plan does not compile: then
    # the TeamPlan as a whole goes back to the planner.
    must_replan: dict[str, tuple[Violation, ...]]
    # The inputs, kept so that the same check can run on a changed plan (recheck).
    team_plan: TeamPlan
    sim_map: SimMap
    start_nodes: Mapping[str, str]
    ledger: FairnessLedger | None
    settings: CheckSettings

    @property
    def approved(self) -> bool:
        return not self.findings

    def recheck(self, team_plan: TeamPlan) -> TeamCheckResult:
        """Run the same check (map, start nodes, ledger, settings) on another plan,
        e.g. this one with a depends_on added."""
        return _check(team_plan, self.sim_map, self.start_nodes, self.ledger, self.settings)


class Conflict(NamedTuple):
    """One S-1 conflict of a result, as R-1 resolved it in that round."""
    reason: str                 # R.CAPACITY_EXCEEDED or R.EDGE_SWAP
    zone: str | None            # None for a head-on swap: it spans two zones
    robots: tuple[str, ...]     # every robot in the conflict, sorted
    yielders: tuple[str, ...]   # the robots R-1 told to yield

    @property
    def keepers(self) -> tuple[str, ...]:
        return tuple(r for r in self.robots if r not in self.yielders)

    @property
    def key(self) -> str:
        """Names the conflict across replan rounds, e.g. 'capacity_exceeded@passage:robot_a+robot_b'."""
        return f"{self.reason}@{self.zone or '-'}:{'+'.join(self.robots)}"


_CONFLICT_REASONS = (R.CAPACITY_EXCEEDED, R.EDGE_SWAP)


def conflicts(result: TeamCheckResult) -> tuple[Conflict, ...]:
    """The S-1 conflicts of a result, one entry each. A head-on swap is reported as
    one finding per zone (EDGE_SWAP); both halves are the same conflict."""
    found: dict[str, Conflict] = {}
    for f in result.findings:
        if f.reason not in _CONFLICT_REASONS:
            continue
        ref = f.violation.refs[0]
        zone = (None if f.reason == R.EDGE_SWAP
                else result.compiled.plans[ref.robot_id].steps[ref.step_index].region)
        c = Conflict(f.reason, zone, tuple(sorted({r.robot_id for r in f.violation.refs})), f.must_fix)
        found.setdefault(c.key, c)
    return tuple(found.values())


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
    settings = CheckSettings(speed=speed, t0=t0, margin=margin, max_duration=max_duration,
                             access_policy=access_policy, task_types=task_types,
                             plan_id=plan_id, revision=revision)
    return _check(team_plan, sim_map, start_nodes, ledger, settings)


def _check(team_plan: TeamPlan, sim_map: SimMap, start_nodes: Mapping[str, str],
           ledger: FairnessLedger | None, s: CheckSettings) -> TeamCheckResult:
    compiled = compile_team_plan(team_plan, sim_map, start_nodes, speed=s.speed, t0=s.t0,
                                 task_types=s.task_types, plan_id=s.plan_id, revision=s.revision)
    inputs = dict(team_plan=team_plan, sim_map=sim_map, start_nodes=start_nodes,
                  ledger=ledger, settings=s)
    if compiled.violations:
        findings = tuple(_finding(v, compiled, ()) for v in compiled.violations)
        return TeamCheckResult(compiled=compiled, findings=findings, must_replan={}, **inputs)

    found: list[tuple[Violation, tuple[str, ...]]] = []   # (violation, robots that must fix it)
    for robot, plan in compiled.plans.items():
        for v in validate_policy(plan, sim_map.world, s.access_policy, s.max_duration):
            found.append((v, (robot,)))

    conflicts, yielders = check_cross_plan_conflicts(
        list(compiled.plans.values()), sim_map.world, ledger, margin=s.margin)
    for v in conflicts:
        found.append((v, tuple(sorted(r for r, vs in yielders.items() if any(v is y for y in vs)))))

    must_replan: dict[str, list[Violation]] = {}
    for v, robots in found:
        for robot in robots:
            must_replan.setdefault(robot, []).append(v)
    return TeamCheckResult(
        compiled=compiled,
        findings=tuple(_finding(v, compiled, robots) for v, robots in found),
        must_replan={robot: tuple(vs) for robot, vs in sorted(must_replan.items())},
        **inputs,
    )


def _finding(violation: Violation, compiled: CompiledTeamPlan, must_fix: tuple[str, ...]) -> Finding:
    task_ids: list[str] = []
    for ref in violation.refs:
        task_id = compiled.origins[ref.robot_id][ref.step_index].task_id
        if task_id is not None and task_id not in task_ids:
            task_ids.append(task_id)
    return Finding(violation, reason_of(violation), tuple(task_ids), must_fix)
