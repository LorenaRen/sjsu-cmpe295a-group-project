"""check_team_plan: the shared checker run on a Module 1 TeamPlan.

  TeamPlan + mrs_sim map + where each robot is
    -> compile (compiler.py)      unknown robot or target, no route, deadlock: stop here
    -> each robot: P-1, P-2, P-3  (policy.validate_policy)
    -> all robots: S-1 + S-2 + S-4 + R-1 (cross_plan.check_cross_plan_conflicts; S-2
                                          when people are given)

A policy violation is for the robot that proposed the step to fix. For a
conflict (S-1, S-2 or S-4), R-1 and the fairness ledger choose which robots give
way, and those replan; TeamPlan priority plays no part. Exceptions, in S-4: when
the keeper stays at the node to the end of its plan and the yielder has to be there
after it (node_presence.only_the_keeper_can_resolve), waiting cannot help, and the
keeper must change its plan instead; likewise when the keeper stands there waiting
for the yielder's task that brings the yielder there (node_presence.keeper_in_the_way:
the yielder waiting would close a cycle). The same goes for a yielder whose part
of the conflict is on the edge it is on when the plan starts (a lead-in step,
compiler.OnEdge): no plan changes where a robot drives now. The ledger is only
read here: recording yields belongs to whatever runs the replan loop.

P-2 leaves out lead-in steps: a robot already driving through a zone it may not
enter is there whatever the plan says.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Collection, Mapping, NamedTuple, Sequence

from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan

from ..cross_plan import ConflictMemo, FairnessLedger, check_cross_plan_conflicts
from ..crowding import CrowdingLimit
from ..plan import HumanObservation, RegionKind, TaskType, Violation
from ..policy import DEFAULT_MAX_DURATION, AccessPolicy, validate_policy
from .compiler import CompiledTeamPlan, OnEdge, compile_team_plan
from .node_presence import keeper_in_the_way, only_the_keeper_can_resolve, presence
from .sim_map import SimMap

# The map only labels a zone "restricted"; this is the rule that goes with the
# label unless the caller passes another policy: no robot may enter.
NO_ENTRY_TO_RESTRICTED = AccessPolicy(by_kind={RegionKind.RESTRICTED: frozenset()})


_RULE_NAMES = {"s1": "S-1", "s2": "S-2", "s4": "S-4", "p1": "P-1", "p2": "P-2", "p3": "P-3"}


@dataclass(frozen=True)
class Rules:
    """Which rules check_team_plan runs (rules_v1.md). Compile errors (an unknown robot or
    target, no route, a deadlock) are always reported: without a compiled plan nothing else
    can be checked. The default is every rule: a caller that names no rules gets the full
    check, which fails closed. The gate chooses its own set (mrs_ethical_gate settings)."""
    s1: bool = True   # zone capacity, with head-on swaps (EDGE_SWAP)
    s2: bool = True   # robots crowding a person (when there are people)
    s4: bool = True   # node occupancy
    p1: bool = True   # final stop in a passthrough zone
    p2: bool = True   # region access
    p3: bool = True   # longest plan

    def enabled(self) -> list[str]:
        return [_RULE_NAMES[f] for f in _RULE_NAMES if getattr(self, f)]

    def disabled(self) -> list[str]:
        return [_RULE_NAMES[f] for f in _RULE_NAMES if not getattr(self, f)]


ALL_RULES = Rules()


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
    crowding: CrowdingLimit | None   # S-2's R and N; None when no people are given
    robot_length: float              # [m] along the direction of travel (S-4)
    rules: Rules = ALL_RULES


class Finding(NamedTuple):
    violation: Violation
    reason: str                # mrs_common.reasons code, e.g. "capacity_exceeded"
    task_ids: tuple[str, ...]  # TeamPlan tasks that the violation's steps came from
    must_fix: tuple[str, ...]  # robots that have to change their plan; () if it does not compile
    # For a conflict, the robots R-1 chose to give way (the others keep); () otherwise. The
    # same as must_fix, except in S-4 where only the keeper can resolve it.
    r1_yielders: tuple[str, ...] = ()


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
    on_edges: Mapping[str, OnEdge]         # robots between two nodes; {} if none
    blocked_edges: tuple[tuple[str, str], ...]   # edges no route may use, each pair sorted
    people: tuple[HumanObservation, ...]   # where people are, for S-2
    ledger: FairnessLedger | None
    settings: CheckSettings
    # What this check and its rechecks share (cross_plan.ConflictMemo); not part of the result.
    memo: ConflictMemo | None = field(default=None, compare=False, repr=False)

    @property
    def approved(self) -> bool:
        return not self.findings

    def recheck(self, team_plan: TeamPlan) -> TeamCheckResult:
        """Run the same check (map, where the robots are, people, ledger, settings) on
        another plan, e.g. this one with a depends_on added. It reuses what such a change
        cannot change (compile_team_plan's base), and gives what recheck_full gives."""
        return _check(team_plan, self.sim_map, self.start_nodes, self.on_edges, self.blocked_edges,
                      self.people, self.ledger, self.settings, base=self, memo=self.memo)

    def recheck_full(self, team_plan: TeamPlan) -> TeamCheckResult:
        """recheck, with everything computed again from scratch: what recheck must give."""
        return _check(team_plan, self.sim_map, self.start_nodes, self.on_edges, self.blocked_edges,
                      self.people, self.ledger, self.settings)


class Conflict(NamedTuple):
    """One S-1, S-2 or S-4 conflict of a result, as R-1 resolved it in that round."""
    reason: str                 # R.CAPACITY_EXCEEDED, R.EDGE_SWAP, R.HUMAN_CROWDING or R.NODE_OCCUPIED
    where: str | None           # the zone (S-1), the person (S-2) or the node (S-4); None for
                                # a head-on swap, which spans two zones
    robots: tuple[str, ...]     # every robot in the conflict, sorted
    yielders: tuple[str, ...]   # the robots that give way: must change their plan (Finding.must_fix)

    @property
    def keepers(self) -> tuple[str, ...]:
        return tuple(r for r in self.robots if r not in self.yielders)

    @property
    def key(self) -> str:
        """Names the conflict across replan rounds, e.g. 'capacity_exceeded@passage:robot_a+robot_b'
        or 'human_crowding@person_1:robot_a+robot_b'."""
        return f"{self.reason}@{self.where or '-'}:{'+'.join(self.robots)}"


_CONFLICT_REASONS = (R.CAPACITY_EXCEEDED, R.EDGE_SWAP, R.HUMAN_CROWDING, R.NODE_OCCUPIED)


def conflicts(result: TeamCheckResult) -> tuple[Conflict, ...]:
    """The S-1, S-2 and S-4 conflicts of a result, one entry each. A head-on swap is reported
    as one finding per zone (EDGE_SWAP); both halves are the same conflict."""
    found: dict[str, Conflict] = {}
    for f in result.findings:
        if f.reason not in _CONFLICT_REASONS:
            continue
        where = None if f.reason == R.EDGE_SWAP else f.violation.resource_id
        c = Conflict(f.reason, where, tuple(sorted({r.robot_id for r in f.violation.refs})), f.must_fix)
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
    robot_length: float,
    t0: float = 0.0,
    ledger: FairnessLedger | None = None,
    access_policy: AccessPolicy = NO_ENTRY_TO_RESTRICTED,
    margin: float = 0.5,
    max_duration: float = DEFAULT_MAX_DURATION,
    task_types: Mapping[str, TaskType] | None = None,
    plan_id: str = "teamplan",
    revision: int = 0,
    people: Sequence[HumanObservation] = (),
    crowding: CrowdingLimit | None = None,
    on_edges: Mapping[str, OnEdge] | None = None,
    blocked_edges: Collection[tuple[str, str]] = (),
    rules: Rules = ALL_RULES,
) -> TeamCheckResult:
    """Compile a TeamPlan and run the policy and cross-plan checks on it.

    speed, t0, task_types, plan_id, revision, on_edges (robots between two
    nodes, which first drive to their start node) and blocked_edges (edges no
    route may use) go to compile_team_plan;
    margin to S-1, S-2 and S-4; access_policy and max_duration to P-2 and P-3;
    people (where they stand now) and crowding (R and N) to S-2, which runs
    only when there are people; rules, which of them run (every one by default).
    robot_length [m] has no default, like speed: S-4
    counts a robot at a node while any part of it is over the node, for
    robot_length / speed seconds as it drives over.
    """
    if people and crowding is None and rules.s2:
        raise ValueError("people were given without a CrowdingLimit: S-2 needs R and N")
    if not (math.isfinite(robot_length) and robot_length > 0):
        raise ValueError(f"robot_length must be a length > 0 m, got {robot_length}")
    settings = CheckSettings(speed=speed, t0=t0, margin=margin, max_duration=max_duration,
                             access_policy=access_policy, task_types=task_types,
                             plan_id=plan_id, revision=revision, crowding=crowding,
                             robot_length=robot_length, rules=rules)
    blocked = tuple(sorted({tuple(sorted(e)) for e in blocked_edges}))
    return _check(team_plan, sim_map, start_nodes, dict(on_edges or {}), blocked, tuple(people), ledger,
                  settings, memo=ConflictMemo())


def _check(team_plan: TeamPlan, sim_map: SimMap, start_nodes: Mapping[str, str],
           on_edges: Mapping[str, OnEdge], blocked_edges: tuple[tuple[str, str], ...],
           people: tuple[HumanObservation, ...], ledger: FairnessLedger | None,
           s: CheckSettings, base: TeamCheckResult | None = None,
           memo: ConflictMemo | None = None) -> TeamCheckResult:
    compiled = compile_team_plan(team_plan, sim_map, start_nodes, speed=s.speed, t0=s.t0,
                                 task_types=s.task_types, plan_id=s.plan_id, revision=s.revision,
                                 on_edges=on_edges, blocked_edges=blocked_edges,
                                 base=base.compiled if base is not None else None,
                                 base_plan=base.team_plan if base is not None else None)
    inputs = dict(team_plan=team_plan, sim_map=sim_map, start_nodes=start_nodes, on_edges=on_edges,
                  blocked_edges=blocked_edges, people=people, ledger=ledger, settings=s, memo=memo)
    if compiled.violations:
        findings = tuple(_finding(v, compiled, ()) for v in compiled.violations)
        return TeamCheckResult(compiled=compiled, findings=findings, must_replan={}, **inputs)

    # (violation, robots that must fix it, the robots R-1 chose to give way)
    found: list[tuple[Violation, tuple[str, ...], tuple[str, ...]]] = []
    for robot, plan in compiled.plans.items():
        def run_policy(plan=plan):
            return validate_policy(plan, sim_map.world, s.access_policy, s.max_duration,
                                   p1=s.rules.p1, p2=s.rules.p2, p3=s.rules.p3)
        policy = run_policy() if memo is None else memo.per_plan("policy", plan, run_policy)
        for v in policy:
            if v.code == "REGION_ACCESS_DENIED" and all(compiled.is_lead_in(robot, r.step_index)
                                                        for r in v.refs):
                continue   # it is in there already, on the edge it is on
            found.append((v, (robot,), ()))

    conflicts, yielders = check_cross_plan_conflicts(
        list(compiled.plans.values()), sim_map.world, ledger, margin=s.margin,
        people=people if s.rules.s2 else (), crowding=s.crowding, pass_time=s.robot_length / s.speed, memo=memo,
        capacity=s.rules.s1, node_occupancy=s.rules.s4)
    # The robots R-1 chose for each conflict, by the violation object R-1 listed (equal violations of
    # two conflicts would still be two conflicts): one pass over yielders, not one per conflict.
    chosen: dict[int, set[str]] = {}
    for robot, vs in yielders.items():
        for y in vs:
            chosen.setdefault(id(y), set()).add(robot)
    for v in conflicts:
        r1 = tuple(sorted(chosen.get(id(v), ())))
        found.append((v, _who_must_change(v, r1, compiled, team_plan), r1))

    must_replan: dict[str, list[Violation]] = {}
    for v, robots, _ in found:
        for robot in robots:
            must_replan.setdefault(robot, []).append(v)
    return TeamCheckResult(
        compiled=compiled,
        findings=tuple(_finding(v, compiled, robots, r1) for v, robots, r1 in found),
        must_replan={robot: tuple(vs) for robot, vs in sorted(must_replan.items())},
        **inputs,
    )


def _who_must_change(violation: Violation, r1_yielders: tuple[str, ...], compiled: CompiledTeamPlan,
                     team_plan: TeamPlan) -> tuple[str, ...]:
    """For each yielder and keeper of a conflict: the yielder, or the keeper if the yielder
    cannot keep clear: its part of the conflict is on the edge it is on (a lead-in step), or,
    in S-4, only the keeper can resolve it."""
    steps: dict[str, list[int]] = {}
    for ref in violation.refs:
        steps.setdefault(ref.robot_id, []).append(ref.step_index)
    must: set[str] = set()
    for y in r1_yielders:
        for k in sorted(set(steps) - set(r1_yielders)):
            must.add(k if _keeper_must_change(violation, compiled, team_plan, y, steps[y], k, steps[k]) else y)
    return tuple(sorted(must))


def _keeper_must_change(violation: Violation, compiled: CompiledTeamPlan, team_plan: TeamPlan,
                        y: str, y_steps: list[int], k: str, k_steps: list[int]) -> bool:
    if any(compiled.is_lead_in(y, step) for step in y_steps):
        return True
    if violation.code != "NODE_OCCUPIED":
        return False
    node = violation.resource_id
    y_at = presence(compiled, team_plan, y, min(y_steps), node)
    k_at = presence(compiled, team_plan, k, max(k_steps), node)
    return only_the_keeper_can_resolve(y_at, k_at) or keeper_in_the_way(team_plan, y_at, k_at)


def _finding(violation: Violation, compiled: CompiledTeamPlan, must_fix: tuple[str, ...],
             r1_yielders: tuple[str, ...] = ()) -> Finding:
    task_ids: list[str] = []
    for ref in violation.refs:
        task_id = compiled.origins[ref.robot_id][ref.step_index].task_id
        if task_id is not None and task_id not in task_ids:
            task_ids.append(task_id)
    return Finding(violation, reason_of(violation), tuple(task_ids), must_fix, r1_yielders)
