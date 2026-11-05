"""EthicalVerdict content (Module 5) from a check_team_plan result. REJECT path only.

  finding                            constraint (mrs_common.plan_schema.Constraint)
  S-1 capacity_exceeded, edge_swap   order_before: the keeper's task that takes it out of
                                     the zone finishes before the yielder's task that
                                     takes it in (verified first, see below)
  S-2 human_crowding                 the same, with "away from the person" for "out of the
                                     zone" and "near them" for "in"
  S-4 node_occupied                  order_before (the keeper's task that takes it away from
                                     the node, the yielder's task that brings it there),
                                     verified first; if it does not help, forbid_node, or
                                     custom when the node is where the yielder's task ends or
                                     the yielder drives through it. If the keeper stays to the
                                     end of its plan and the yielder has to be there, only the
                                     keeper can free the node (team_check's must_fix): it waits
                                     until a yielder driving through has gone by, verified on
                                     the whole plan, or else ends somewhere else (forbid_node);
                                     for a yielder whose task ends there, custom
  P-1 final_stop_in_passthrough      forbid_node for every node of the zone
  P-2 region_access_denied           avoid_node for every node of the zone; custom if it has none
  P-3 and compile errors             custom
  plan_json is not a TeamPlan        custom, rule llm_malformed (unreadable_plan_verdict)
  the gate does not know where       custom, rule unknown_robot (unknown_positions_verdict):
  every robot is                     nothing is checked

Every node of the zone, not only the one in the plan: otherwise the next plan
can stop at, or route through, a neighbouring node of the same zone and be
rejected again.

An order_before is not always a fix. The keeper may stay in the zone to the end
of its plan, the yielder may already be in it, the ordering may close a cycle
with the plan's depends_on, or the plan may already have it (the conflict then
comes from the time margin). So it is verified before it is suggested: added as
a depends_on to a copy of the TeamPlan, which is checked again. If the conflict
between these two robots in this zone is gone, the order_before is sent;
otherwise a custom constraint says why ordering them does not help. S-2 works the
same way, with "near this person" for "in this zone"; its texts name the person's
nearest node, because the planner knows nodes, not where people stand.

The check again is TeamCheckResult.recheck, which reuses what an added depends_on
cannot change: every route, the plans of robots whose task times stay the same, and
their claims, policy findings and sweeps (compiler.py, cross_plan.ConflictMemo). It
gives exactly what checking the copy from scratch (recheck_full) gives; the tests
compare the two on every recheck, and the benchmarks on every verdict of their
scenarios.

The verification looks only at that pair of robots in that zone. It does not
guarantee that the reordered plan creates no new conflict elsewhere: on the
REJECT path the next round's check catches those; MODIFY (designed, not built)
must check the whole modified plan again before approving it.

A robot on an edge when the plan starts (team_state; compiler.OnEdge) first
drives the rest of it, which no plan changes. A keeper that leaves the place on
it is waited for until its first task is done: no task event marks the end of
the edge. A yielder whose part of the conflict is on it cannot wait, so the
keeper is told to keep clear instead (custom; team_check's must_fix is the keeper).

Each ordering is verified on its own, so at the end every order_before of the
verdict is added to one copy of the plan, and checked for tasks waiting for each
other in a cycle (depends_on and each robot's task order, compiler.deadlock). If
they would, the S-4 keepers' waits, the only orderings that make the robot R-1
ranked higher wait, fall back to forbid_node, last first, until there is no
cycle; a cycle still left can only be closed by the plan's own depends_on, and
then the orderings, last first, become custom constraints (_without_cycles).

rule_ids are mrs_common.reasons codes. risk_score is an ordinal level, not a
probability (SEVERITY). standard_refs stay empty: the team has not read the
standards' text yet, and EthicalVerdict only allows clauses that were read.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, NamedTuple, Sequence

from mrs_common import reasons as R
from mrs_common.plan_schema import Constraint, TeamPlan
from pydantic import ValidationError

from ..plan import RegionKind
from .compiler import deadlock
from .node_presence import Presence, keeper_in_the_way, only_the_keeper_can_resolve, presence
from .team_check import Finding, TeamCheckResult

# The strings of EthicalVerdict.VERDICT_* (mrs_msgs needs a ROS build to import).
APPROVE = "APPROVE"
MODIFY = "MODIFY"    # the checker repairing a plan itself: designed, not built yet
REJECT = "REJECT"

# Ordinal severity of a finding; risk_score = worst level / TOP_LEVEL. Only the
# order means anything: this is not a probability and must not be used as one.
SEVERITY: dict[str, int] = {
    R.CAPACITY_EXCEEDED: 3,          # robots predicted to collide or block each other
    R.EDGE_SWAP: 3,
    R.HUMAN_CROWDING: 3,             # placeholder: ranked with S-1 until the team decides
    R.NODE_OCCUPIED: 3,              # two robots cannot stand at one point
    R.FINAL_STOP_IN_PASSTHROUGH: 2,  # a rule about where robots may be is broken
    R.REGION_ACCESS_DENIED: 2,
    R.MAX_DURATION_EXCEEDED: 1,      # the plan cannot run as written, or runs too long
    R.UNKNOWN_ROBOT: 1,
    R.UNKNOWN_TARGET: 1,
    R.NO_ROUTE: 1,
    R.DEPENDENCY_DEADLOCK: 1,
    R.LLM_MALFORMED: 1,              # plan_json is not a TeamPlan (no code of its own yet)
}
TOP_LEVEL = 3

MAX_PROBLEMS_IN_RATIONALE = 3

_S1_CODES = frozenset({"CAPACITY_EXCEEDED", "EDGE_SWAP"})
_CONFLICT_CODES = _S1_CODES | {"HUMAN_CROWDING", "NODE_OCCUPIED"}

# How P-1 describes the zone a robot must not stop in.
_STAY_CLEAR = {
    RegionKind.AISLE: "narrow passage",
    RegionKind.DOORWAY: "doorway",
    RegionKind.KEEP_CLEAR: "keep-clear zone",
}


@dataclass(frozen=True)
class VerdictContent:
    """The decision fields of an EthicalVerdict; the gate node adds ids, latency and stamp."""
    verdict: str
    rule_ids: tuple[str, ...]
    standard_refs: tuple[str, ...]
    risk_score: float
    rationale: str
    constraints: tuple[Constraint, ...]
    # What situation each constraint answers, in the same order, e.g. "yielder_waits" (see
    # _stop_constraint). Not part of the message: the benchmarks count by it.
    cases: tuple[str, ...] = ()

    def make_verdict_kwargs(self) -> dict[str, Any]:
        """Arguments for mrs_common.convert.make_verdict, besides transaction_id,
        episode_id, attempt, latency_ms and node."""
        return {"verdict": self.verdict, "rule_ids": list(self.rule_ids),
                "standard_refs": list(self.standard_refs), "risk_score": self.risk_score,
                "rationale": self.rationale, "constraints": list(self.constraints)}


def to_verdict(result: TeamCheckResult) -> VerdictContent:
    """APPROVE when nothing was found, else REJECT with one or more constraints per finding."""
    if result.approved:
        return VerdictContent(APPROVE, (), (), 0.0, "Plan approved: no rule violations found.", ())
    proposals: list[_Proposal] = []
    for finding in result.findings:
        proposals += _constraints_for(finding, result)
    proposals = _one_per_demand(proposals)
    chosen = _one_per_demand(_labelled(proposals, _without_cycles(result.team_plan, proposals)))
    return VerdictContent(
        verdict=REJECT,
        rule_ids=tuple(sorted({f.reason for f in result.findings})),
        standard_refs=(),
        risk_score=max(SEVERITY[f.reason] for f in result.findings) / TOP_LEVEL,
        rationale=_rationale(result),
        constraints=tuple(p.constraint for p in chosen),
        cases=tuple(p.case for p in chosen),
    )


class _Proposal(NamedTuple):
    """A constraint for the verdict, and what it becomes if the verdict's orderings, taken
    together, would make tasks wait for each other in a cycle (S-4's keeper's wait); with the
    situation each answers (VerdictContent.cases)."""
    constraint: Constraint
    fallback: Constraint | None = None
    case: str = ""
    fallback_case: str = ""


def _labelled(proposals: list[_Proposal], chosen: list[Constraint]) -> list[_Proposal]:
    """_without_cycles' constraints, one for each proposal, with the situation each answers: the
    proposal's, its fallback's, or "cycle" for an ordering it turned into a custom constraint."""
    return [_Proposal(c, case=p.case if c is p.constraint else p.fallback_case if c is p.fallback else "cycle")
            for p, c in zip(proposals, chosen)]


def _one_per_demand(items: list[_Proposal]) -> list[_Proposal]:
    """The first proposal of each demand, in order."""
    out, asked = [], set()
    for item in items:
        demand = _demand(item.constraint)
        if demand not in asked:
            asked.add(demand)
            out.append(item)
    return out


def _without_cycles(team_plan: TeamPlan, proposals: list[_Proposal]) -> list[Constraint]:
    """The proposals' constraints, with no set of order_before that would make tasks wait for
    each other in a cycle once added together. First the keepers' waits (with a fallback)
    give way, last first: they are the only orderings that make the robot R-1 ranked higher
    wait. The others each have a lower-ranked robot wait for a higher-ranked one, so with
    the robots' own task order they form no cycle; only the plan's own depends_on can close
    one, and then they give way too, last first, as custom constraints."""
    chosen = [p.constraint for p in proposals]
    for i in reversed(range(len(proposals))):
        if not _deadlocked(team_plan, chosen):
            return chosen
        if proposals[i].fallback is not None:
            chosen[i] = proposals[i].fallback
    for i in reversed(range(len(chosen))):
        if not _deadlocked(team_plan, chosen):
            return chosen
        if chosen[i].kind == "order_before":
            c = chosen[i]
            chosen[i] = Constraint(
                kind="custom", rule_id=c.rule_id, robot_id=c.robot_id, task_id=c.value,
                text=f"{c.text} This cannot be done together with the plan's own depends_on and this "
                     f"verdict's other orderings: the tasks would wait for each other in a cycle. Change "
                     f"the plan so that {c.robot_id} does not have to wait for task {c.task_id}.")
    return chosen


def _deadlocked(team_plan: TeamPlan, constraints: list[Constraint]) -> bool:
    """Whether the order_before of `constraints`, added together to team_plan, make tasks wait
    for each other in a cycle: through depends_on (TeamPlan's own check), or through a robot's
    task order, which TeamPlan does not see (compiler.deadlock)."""
    data = team_plan.model_dump(mode="json")
    tasks = {t["task_id"]: t for a in data["assignments"] for t in a["tasks"]}
    for c in constraints:
        if c.kind == "order_before" and c.task_id not in tasks[c.value]["depends_on"]:
            tasks[c.value]["depends_on"].append(c.task_id)
    try:
        together = TeamPlan.model_validate(data)
    except ValidationError:
        return True
    return deadlock(together) is not None


def _demand(c: Constraint) -> tuple:
    """What a constraint asks for. Two rules can ask for the same change (at a crossing next to
    a person, S-2 and S-4 the same ordering): it is sent once, the first rule's. A custom
    constraint asks for what its text says."""
    if c.kind == "custom":
        return (c.kind, c.robot_id, c.text)
    return (c.kind, c.robot_id, c.task_id, c.node, c.value)


def unreadable_plan_verdict(error: ValueError) -> VerdictContent:
    """REJECT for a ProposedPlan whose plan_json is not a TeamPlan, so nothing could be checked.

    `error` is what mrs_common's plan_from_json raised (pydantic's ValidationError is a
    ValueError). The rule id is llm_malformed, the closest code mrs_common has; a code of
    its own is a TODO for Module 1 (rules_v1.md).
    """
    problem = _describe_read_error(error)
    return VerdictContent(
        verdict=REJECT,
        rule_ids=(R.LLM_MALFORMED,),
        standard_refs=(),
        risk_score=SEVERITY[R.LLM_MALFORMED] / TOP_LEVEL,
        rationale=(f"Plan rejected: plan_json is not a valid TeamPlan ({problem}). "
                   "Propose a new plan that follows the constraints."),
        constraints=(Constraint(kind="custom", rule_id=R.LLM_MALFORMED,
                                text=f"plan_json is not a valid TeamPlan: {problem}. "
                                     "Send a plan that matches the TeamPlan JSON schema."),),
        cases=("unreadable_plan",),
    )


def unknown_positions_verdict(problems: Sequence[tuple[str | None, str]]) -> VerdictContent:
    """REJECT without a check, because the gate does not know where every robot of the fleet is:
    fail closed, since a robot left out could be in the way of any plan.

    problems: (robot, or None when it is about every robot, why its position is unknown), from
    the gate's start source. The rule id is unknown_robot, mrs_common's code for a robot whose
    position is unknown. Each problem becomes a custom constraint: the planner cannot fix it,
    only send the plan again later.
    """
    robots = sorted({robot for robot, _ in problems if robot is not None})
    if any(robot is None for robot, _ in problems):
        who = "the robots are"
    else:
        who = f"{robots[0]} is" if len(robots) == 1 else f"{_join(robots)} are"
    return VerdictContent(
        verdict=REJECT,
        rule_ids=(R.UNKNOWN_ROBOT,),
        standard_refs=(),
        risk_score=SEVERITY[R.UNKNOWN_ROBOT] / TOP_LEVEL,
        rationale=f"Plan not checked: the gate does not know where {who}. Send the plan again once it does.",
        constraints=tuple(
            Constraint(kind="custom", rule_id=R.UNKNOWN_ROBOT, robot_id=robot,
                       text=f"The gate does not know where {robot + ' is' if robot else 'the robots are'}: "
                            f"{why}. Send the plan again once it does.")
            for robot, why in problems),
        cases=("unknown_position",) * len(problems),
    )


def _describe_read_error(error: ValueError) -> str:
    """The first problem, located the way plan_echo does it: 'assignments.0.tasks.0.action: ...'."""
    if not isinstance(error, ValidationError):
        return str(error)
    errors = error.errors()
    where = ".".join(str(p) for p in errors[0]["loc"])
    text = f"{where}: {errors[0]['msg']}" if where else errors[0]["msg"]
    if len(errors) > 1:
        text += f" (and {len(errors) - 1} more error{'s' if len(errors) > 2 else ''})"
    return text


# --------------------------------------------------------------------------
# Constraints
# --------------------------------------------------------------------------

def _constraints_for(f: Finding, result: TeamCheckResult) -> list[_Proposal]:
    code = f.violation.code
    if code == "NODE_OCCUPIED":
        return _stop_constraints(f, result)
    if code in _CONFLICT_CODES:
        constraints, case = _orderings(f, result), "ordering"
    elif code == "FINAL_STOP_IN_PASSTHROUGH":
        constraints, case = _forbid_stops(f, result), "stop"
    elif code == "REGION_ACCESS_DENIED":
        constraints, case = _avoid_zone(f, result), "access"
    elif code == "MAX_DURATION_EXCEEDED":
        robot = f.violation.refs[0].robot_id
        constraints, case = [Constraint(kind="custom", rule_id=f.reason, robot_id=robot,
                                        text=f"{robot}: {_too_long(robot, result)}; make the plan shorter.")], \
            "duration"
    else:   # compile errors: the message already says what is wrong, and with which task
        constraints, case = [Constraint(kind="custom", rule_id=f.reason, text=f.violation.message)], "compile"
    return [_Proposal(c, case=case) for c in constraints]


def _forbid_stops(f: Finding, result: TeamCheckResult) -> list[Constraint]:
    ref = f.violation.refs[0]
    robot, zone = ref.robot_id, _zone(f, result)
    task_id = result.compiled.origins[robot][ref.step_index].task_id
    what = f"a {_STAY_CLEAR[result.sim_map.world.regions[zone].kind]} that must stay clear"
    return [Constraint(kind="forbid_node", rule_id=f.reason, robot_id=robot, task_id=task_id, node=node,
                       text=f"{robot}: do not stop at {node}; it is in {zone}, {what}.")
            for node in _zone_nodes(zone, result)]


def _avoid_zone(f: Finding, result: TeamCheckResult) -> list[Constraint]:
    robot, zone = f.violation.refs[0].robot_id, _zone(f, result)
    nodes = _zone_nodes(zone, result)
    if not nodes:   # e.g. a strip in a doorway that an edge only passes through
        return [Constraint(kind="custom", rule_id=f.reason, robot_id=robot,
                           text=f"{robot}: do not enter {zone}; {robot} may not go there.")]
    return [Constraint(kind="avoid_node", rule_id=f.reason, robot_id=robot, node=node,
                       text=f"{robot}: do not drive through {node}; {robot} may not enter {zone}.")
            for node in nodes]


class _Where(NamedTuple):
    """How the place of a conflict is worded: a zone (S-1), or a person's surroundings (S-2)."""
    first: str      # first mention: "in passage" / "within 1.5 m of person_1 (nearest node c)"
    inside: str     # later mentions: "in passage" / "within 1.5 m of person_1"
    because: str    # why the robots must take turns
    but: str        # the limit, after "at the same time, "
    stay_out: str   # what the yielder should do instead


def _where(f: Finding, result: TeamCheckResult) -> _Where:
    place = f.violation.resource_id
    if f.violation.code == "HUMAN_CROWDING":
        limit = result.settings.crowding
        r, most = f"{limit.radius:g} m", _robots(limit.max_robots)
        near = f"{place} (nearest node {_nearest_node(place, result)})"
        return _Where(first=f"within {r} of {near}", inside=f"within {r} of {place}",
                      because=f"both would be within {r} of {near}, where at most {most} may be at once",
                      but=f"but at most {most} may be", stay_out=f"keeps more than {r} from {place}")
    holds = _robots(result.sim_map.world.capacity(place))
    return _Where(first=f"in {place}", inside=f"in {place}", because=f"both need {place}, which holds {holds}",
                  but=f"but it holds {holds}", stay_out=f"stays out of {place}")


def _orderings(f: Finding, result: TeamCheckResult) -> list[Constraint]:
    """One constraint for each (yielder, keeper) pair of the conflict."""
    where = _where(f, result)
    steps = _steps_by_robot(f)
    return [_ordering(f, result, where, keeper=k, k_steps=steps[k], yielder=y, y_steps=steps[y])
            for y in f.r1_yielders for k in sorted(set(steps) - set(f.r1_yielders))]


def _steps_by_robot(f: Finding) -> dict[str, list[int]]:
    steps: dict[str, list[int]] = {}
    for ref in f.violation.refs:
        steps.setdefault(ref.robot_id, []).append(ref.step_index)
    return steps


def _on_its_edge(result: TeamCheckResult, robot: str, steps: list[int]) -> bool:
    """Whether a robot's part of a conflict is on the edge it is on now (team_check: then the
    other robot must change)."""
    return any(result.compiled.is_lead_in(robot, step) for step in steps)


def _ordering(f: Finding, result: TeamCheckResult, where: _Where, *,
              keeper: str, k_steps: list[int], yielder: str, y_steps: list[int]) -> Constraint:
    k_step, y_step = max(k_steps), min(y_steps)
    origins = result.compiled.origins
    if _on_its_edge(result, yielder, y_steps):   # it cannot wait, so the keeper must keep clear
        return Constraint(
            kind="custom", rule_id=f.reason, robot_id=keeper, task_id=origins[keeper][min(k_steps)].task_id,
            text=f"{keeper}: {yielder} will be {where.inside} on the edge it is on now, so it cannot wait, "
                 f"and you would be there at the same time, {where.but}. Change the plan so that you are not "
                 f"{where.inside} while {yielder} is there.")
    k_task = origins[keeper][k_step].task_id    # takes the keeper out of the place
    if result.compiled.is_lead_in(keeper, k_step):
        # It leaves on the edge it is on. No task event marks that, so the first one after it.
        tasks = result.team_plan.tasks_of(keeper)
        k_task = tasks[0].task_id if tasks else None
    y_task = origins[yielder][y_step].task_id   # takes the yielder into it
    why_not = _why_ordering_fails(f, result, where, keeper, k_task, k_step, yielder, y_task, y_step)
    if why_not is None:
        # A swap is reported once per zone; leaving the zone out of its text makes
        # both halves the same constraint, so the planner gets it once.
        because = ("otherwise they meet head-on and cannot pass each other"
                   if f.violation.code == "EDGE_SWAP" else where.because)
        return Constraint(
            kind="order_before", rule_id=f.reason, robot_id=yielder, task_id=k_task, value=y_task,
            text=f"{yielder}: start task {y_task} only after {keeper} has finished task {k_task}; "
                 f"{because}.")
    return Constraint(
        kind="custom", rule_id=f.reason, robot_id=yielder, task_id=y_task,
        text=f"{yielder} and {keeper} would be {where.first} at the same time, {where.but}, "
             f"and making {yielder} wait for {keeper} does not fix it: {why_not}. "
             f"Change the plan so that {yielder} {where.stay_out} while {keeper} is there.")


def _why_ordering_fails(f: Finding, result: TeamCheckResult, where: _Where, keeper: str,
                        k_task: str | None, k_step: int, yielder: str, y_task: str | None,
                        y_step: int) -> str | None:
    """None if making y_task wait for k_task ends this pair's conflict in this zone (or near
    this person), otherwise the reason it does not. Other robots and places are not looked at."""
    if k_task is None or y_task is None:
        return f"{keeper if k_task is None else yielder} has no task in this plan"
    again = _reordered(result, before=k_task, after=y_task)
    if again is None:
        return "the tasks would wait for each other in a cycle"
    if not _in_conflict(again, f, keeper, yielder):
        return None
    if k_step == len(result.compiled.plans[keeper].steps) - 1:
        return f"{keeper} stays {where.inside} until the end of its plan"
    if y_step == 0:
        return f"{yielder} is already {where.inside} when its plan starts"
    return f"{yielder} would still be {where.inside} while {keeper} is there"


def _reordered(result: TeamCheckResult, *, before: str, after: str) -> TeamCheckResult | None:
    """The same check on the plan in which task `after` also waits for task `before`, or
    None if the tasks would then wait for each other in a cycle."""
    reordered = _with_dependency(result.team_plan, before=before, after=after)
    if reordered is None:
        return None
    again = result.recheck(reordered)
    return None if again.compiled.violations else again   # an added dependency can only make a deadlock


def _stop_constraints(f: Finding, result: TeamCheckResult) -> list[_Proposal]:
    """S-4: one constraint for each (yielder, keeper) pair at the node, as R-1 chose them."""
    steps = _steps_by_robot(f)
    return [_stop_constraint(f, result, keeper=k, k_step=max(steps[k]), yielder=y, y_steps=steps[y])
            for y in f.r1_yielders for k in sorted(set(steps) - set(f.r1_yielders))]


def _stop_constraint(f: Finding, result: TeamCheckResult, *, keeper: str, k_step: int,
                     yielder: str, y_steps: list[int]) -> _Proposal:
    """A yielder that stands where it starts, waiting for its task, waits somewhere else; a
    keeper that stands there waiting for the yielder's task that brings the yielder there does
    (the yielder cannot wait for it without a cycle). Then, in order of preference: make the
    yielder come only after the keeper has left (verified, as for S-1). If the keeper stays to the end of its plan and the yielder has to be there,
    only the keeper can free the node: it waits for a yielder that drives through, if that
    works, else ends somewhere else; a yielder whose task ends there gets told, through the
    keeper, that the task cannot be done as it is. A yielder at the node on the edge it is on
    cannot wait: the keeper is told to keep clear. Otherwise the yielder: one that drives
    through is told why waiting does not help; one whose task ends there waits nearby first,
    since forbidding the node would make it give the task up; else it may not wait there."""
    node = f.violation.resource_id
    y = presence(result.compiled, result.team_plan, yielder, min(y_steps), node)
    k = presence(result.compiled, result.team_plan, keeper, k_step, node)

    def make(kind: str, case: str, text: str, **fields: Any) -> _Proposal:
        return _Proposal(Constraint(kind=kind, rule_id=f.reason, robot_id=yielder, text=text, **fields), case=case)

    if y.from_start:   # there from the start of its plan: it cannot come later
        if not y.passes and y.task is not None and not y.ends_task:
            # It stands where it starts, waiting before task y.task: it can wait somewhere else.
            return make("forbid_node", "from_start",
                        f"{yielder}: do not wait at {node}, where you start; {keeper} would "
                        f"{'drive through it' if k.passes else 'stop there'} while you do. Wait at another "
                        f"node until task {y.task} may start.",
                        task_id=y.task, node=node)
        is_there = "stands at" if not y.passes else "drives off from"
        return make("custom", "from_start",
                    f"{yielder} {is_there} {node} from the start of its plan, and {keeper} would "
                    f"{'drive through it' if k.passes else 'stop there'} while it does. "
                    f"Have {yielder} leave {node} before {keeper} gets there.", node=node)
    if _on_its_edge(result, yielder, y_steps) and not only_the_keeper_can_resolve(y, k):
        # It cannot wait, so the keeper must keep clear (team_check)
        return _Proposal(Constraint(
            kind="custom", rule_id=f.reason, robot_id=keeper, task_id=k.task, node=node,
            text=f"{keeper}: {yielder} gets to {node} on the edge it is on now, so it cannot wait, and you would "
                 f"{'drive through it' if k.passes else 'be there'} then. Change the plan so that you are not "
                 f"at {node} while {yielder} is there."), case="on_its_edge")
    if keeper_in_the_way(result.team_plan, y, k):   # the keeper must change (team_check)
        return _Proposal(Constraint(
            kind="forbid_node", rule_id=f.reason, robot_id=keeper, task_id=k.task, node=node,
            text=f"{keeper}: do not wait at {node}. {yielder} has to "
                 f"{'drive through' if y.passes else 'get to'} {node} with task {y.arrive_task}, and your "
                 f"task {k.leave_task} cannot start before that is done, so {yielder} cannot wait for you to "
                 f"leave. Wait at another node until task {k.leave_task} may start."), case="keeper_in_the_way")
    why_not = None
    if not k.stays and not y.on_lead_in:
        again = _reordered(result, before=k.leave_task, after=y.arrive_task)
        if again is not None and not _in_conflict(again, f, keeper, yielder):
            return make("order_before", "yielder_waits",
                        f"{yielder}: start task {y.arrive_task} only after {keeper} has finished task "
                        f"{k.leave_task}; {_meeting(y, k)}.",
                        task_id=k.leave_task, value=y.arrive_task)
        why_not = ("the tasks would wait for each other in a cycle" if again is None
                   else f"{yielder} would still be at {node} while {keeper} is there")
    if only_the_keeper_can_resolve(y, k):   # the keeper must change (team_check)
        if y.passes:
            return _keeper_waits_or_ends_elsewhere(f, result, y, k)
        return _Proposal(Constraint(
            kind="custom", rule_id=f.reason, robot_id=keeper, task_id=k.task, node=node,
            text=f"{keeper}: you stay at {node} until the end of your plan, but {node} is where task {y.task} "
                 f"of {yielder} ends, so that task cannot be done with the plan as it is. End somewhere "
                 f"else, so that {node} is free when {yielder} gets there."), case="cannot_be_done")
    if y.passes:
        k_there = (f"is stopped there until it leaves with task {k.leave_task}" if not k.passes
                   else "drives through it")
        return make("custom", "passes_waiting_fails",
                    f"{yielder} would drive through {node} while {keeper} {k_there}, and making {yielder} "
                    f"wait for {keeper} does not fix it: {why_not}. Change the plan so that {yielder} "
                    f"drives through {node} when {keeper} is not there.",
                    task_id=y.task, node=node)
    k_is = ("stays there until the end of its plan" if k.stays
            else "drives through it then" if k.passes
            else f"is stopped there until it leaves with task {k.leave_task}")
    if y.ends_task:
        return make("custom", "wait_nearby",
                    f"{yielder}: {node} is where task {y.task} ends, but {keeper} {k_is}. Wait at another "
                    f"node near {node} first, and go to {node} only after {keeper} has left it.",
                    task_id=y.task, node=node)
    return make("forbid_node", "no_waiting_there", f"{yielder}: do not wait at {node}; {keeper} {k_is}.",
                task_id=y.task, node=node)


def _keeper_waits_or_ends_elsewhere(f: Finding, result: TeamCheckResult, y: Presence,
                                    k: Presence) -> _Proposal:
    """The keeper stays at the node to the end of its plan and the yielder has to drive through
    it. The keeper waits until the yielder has gone by, if that works, else it ends somewhere
    else: forbid_node, which changes its final stop, so the text says why. The wait's fallback,
    if the verdict's orderings together would wait in a cycle, is the same forbid_node."""
    node, yielder, keeper = f.violation.resource_id, y.robot, k.robot

    def end_elsewhere(why: str) -> Constraint:
        return Constraint(
            kind="forbid_node", rule_id=f.reason, robot_id=keeper, task_id=k.task, node=node,
            text=f"{keeper}: do not end your plan at {node}. This changes your final stop, because {yielder} "
                 f"has to drive through {node} (task {y.task}) and you would stay there until the end of your "
                 f"plan; waiting until {yielder} has gone by does not help: {why}.")

    why = _why_waiting_for_the_yielder_fails(f, result, y, k)
    if why is not None:
        return _Proposal(end_elsewhere(why), case="keeper_ends_elsewhere")
    wait = Constraint(kind="order_before", rule_id=f.reason, robot_id=keeper, task_id=y.leave_task,
                      value=k.arrive_task,
                      text=f"{keeper}: start task {k.arrive_task} only after {yielder} has finished task "
                           f"{y.leave_task}; {yielder} has to drive through {node}, where you would stay "
                           f"until the end of your plan.")
    return _Proposal(wait, fallback=end_elsewhere(
        "together with the other orderings of this plan, the tasks would wait for each other in a cycle"),
        case="keeper_waits", fallback_case="keeper_ends_elsewhere")


def _why_waiting_for_the_yielder_fails(f: Finding, result: TeamCheckResult, y: Presence,
                                       k: Presence) -> str | None:
    """None if the keeper's task that brings it to the node can wait for the yielder's task that
    takes it through, otherwise why not. Checked on the whole plan, unlike every other ordering:
    the yielder's orderings only delay its getting to one place, but this one moves all that is
    left of the keeper's plan later, which can make the two robots meet anywhere else. So the
    conflict here must be gone, and the two may meet nowhere they did not meet before."""
    node, yielder, keeper = f.violation.resource_id, y.robot, k.robot
    if k.from_start:
        return f"you are at {node} from the start of your plan"
    if k.on_lead_in:
        return f"you get to {node} on the edge you are on now"
    again = _reordered(result, before=y.leave_task, after=k.arrive_task)
    if again is None:
        return "the tasks would wait for each other in a cycle"
    if _in_conflict(again, f, keeper, yielder):
        return f"{yielder} would still drive through {node} while you are there"
    before = _meetings(result, keeper, yielder)
    new = [where for key, where in _meetings(again, keeper, yielder).items() if key not in before]
    return f"you and {yielder} would then meet {new[0]}" if new else None


def _meetings(result: TeamCheckResult, a: str, b: str) -> dict[tuple[str, str | None], str]:
    """The conflicts between robots a and b: (rule, place) -> where, in words."""
    out: dict[tuple[str, str | None], str] = {}
    for g in result.findings:
        code, place = g.violation.code, g.violation.resource_id
        if code in _CONFLICT_CODES and {a, b} <= {ref.robot_id for ref in g.violation.refs}:
            rule = "S-1" if code in _S1_CODES else code
            out.setdefault((rule, place), {"NODE_OCCUPIED": f"at {place}",
                                           "HUMAN_CROWDING": f"near {place}"}.get(code, f"in {place}"))
    return out


def _meeting(y: Presence, k: Presence) -> str:
    """How the yielder and the keeper would be at the node at once, for an ordering's text."""
    node = y.node
    if not y.passes and not k.passes:
        return f"both would stop at {node}, where only one robot can stand"
    if y.passes and k.passes:
        return f"both would drive through {node} at the same time"
    driver, stander = (y.robot, k.robot) if y.passes else (k.robot, y.robot)
    return f"{driver} would drive through {node} while {stander} stands there"


def _with_dependency(plan: TeamPlan, *, before: str, after: str) -> TeamPlan | None:
    """A copy of plan in which task `after` also depends on task `before`, or None
    if Module 1 rejects that (depends_on would be circular)."""
    data = plan.model_dump(mode="json")
    for assignment in data["assignments"]:
        for task in assignment["tasks"]:
            if task["task_id"] == after and before not in task["depends_on"]:
                task["depends_on"].append(before)
    try:
        return TeamPlan.model_validate(data)
    except ValidationError:
        return None


def _in_conflict(result: TeamCheckResult, f: Finding, a: str, b: str) -> bool:
    """Whether result still has f's conflict: a finding of the same rule, over the same
    zone or person, with both robots in it."""
    rule = _S1_CODES if f.violation.code in _S1_CODES else {f.violation.code}
    return any(g.violation.code in rule and g.violation.resource_id == f.violation.resource_id
               and {a, b} <= {ref.robot_id for ref in g.violation.refs}
               for g in result.findings)


# --------------------------------------------------------------------------
# Rationale
# --------------------------------------------------------------------------

def _rationale(result: TeamCheckResult) -> str:
    """Two sentences: what is wrong, then what to do. The constraints carry the details."""
    problems: list[str] = []
    for f in result.findings:
        problem = _problem(f, result)
        if problem not in problems:
            problems.append(problem)
    shown = problems[:MAX_PROBLEMS_IN_RATIONALE]
    more = len(problems) - len(shown)
    listed = "; ".join(shown)
    if more:
        listed += f"; and {more} more problem{'s' if more > 1 else ''}"
    return f"Plan rejected: {listed}. Propose a new plan that follows the constraints."


def _problem(f: Finding, result: TeamCheckResult) -> str:
    code, refs = f.violation.code, f.violation.refs
    if code == "CAPACITY_EXCEEDED":
        zone = _zone(f, result)
        robots = sorted({ref.robot_id for ref in refs})
        return (f"{_join(robots)} would be in {zone} at the same time, "
                f"but it holds {_robots(result.sim_map.world.regions[zone].capacity)}")
    if code == "HUMAN_CROWDING":
        robots = sorted({ref.robot_id for ref in refs})
        where = _where(f, result)
        return f"{_join(robots)} would be {where.first} at the same time, {where.but}"
    if code == "NODE_OCCUPIED":
        node = f.violation.resource_id
        first: dict[str, int] = {}
        for ref in refs:
            first.setdefault(ref.robot_id, ref.step_index)
        drives = {r: presence(result.compiled, result.team_plan, r, step, node).passes
                  for r, step in first.items()}
        robots = sorted(drives)
        drivers, standers = [r for r in robots if drives[r]], [r for r in robots if not drives[r]]
        if not drivers:
            return f"{_join(robots)} would stop at {node} at the same time"
        if not standers:
            return f"{_join(robots)} would drive through {node} at the same time"
        if len(robots) == 2:
            return f"{drivers[0]} would drive through {node} while {standers[0]} stands there"
        return f"{_join(robots)} would be at {node} at the same time"
    if code == "EDGE_SWAP":
        # Reported once per zone of the swap; the wording leaves the zone out so both halves read the same.
        a, b = sorted({ref.robot_id for ref in refs})
        return f"{a} and {b} would meet head-on and could not pass each other"
    if code == "FINAL_STOP_IN_PASSTHROUGH":
        robot, zone = refs[0].robot_id, _zone(f, result)
        node = result.compiled.origins[robot][refs[0].step_index].nodes[-1]
        kind = _STAY_CLEAR[result.sim_map.world.regions[zone].kind]
        return f"{robot} would stop at {node} in {zone}, a {kind} that must stay clear"
    if code == "REGION_ACCESS_DENIED":
        robot, zone = refs[0].robot_id, _zone(f, result)
        if result.sim_map.world.regions[zone].kind == RegionKind.RESTRICTED:
            return f"{robot}'s route goes through {zone}, a restricted zone"
        return f"{robot}'s route goes through {zone}, where it may not go"
    if code == "MAX_DURATION_EXCEEDED":
        robot = refs[0].robot_id
        return f"{robot}'s {_too_long(robot, result)}"
    return f.violation.message


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------

def _zone(f: Finding, result: TeamCheckResult) -> str:
    """The zone of the finding's steps (all steps of one S-1 conflict share it)."""
    ref = f.violation.refs[0]
    return result.compiled.plans[ref.robot_id].steps[ref.step_index].region


def _nearest_node(person_id: str, result: TeamCheckResult) -> str:
    """The graph node closest to a person (ties: by name)."""
    position = next(p.position for p in result.people if p.human_id == person_id)
    nodes = result.sim_map.positions
    return min(nodes, key=lambda n: (math.dist(nodes[n], position), n))


def _zone_nodes(zone: str, result: TeamCheckResult) -> list[str]:
    return sorted(node for node, z in result.sim_map.zone_of.items() if z == zone)


def _too_long(robot: str, result: TeamCheckResult) -> str:
    plan = result.compiled.plans[robot]
    return (f"plan takes {plan.t_end - plan.t_start:.0f} s, "
            f"more than the {result.settings.max_duration:.0f} s limit")


def _robots(n: int) -> str:
    return "1 robot" if n == 1 else f"{n} robots"


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
