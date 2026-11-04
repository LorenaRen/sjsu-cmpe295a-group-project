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
                                     custom when the node is where the yielder's task ends
  P-1 final_stop_in_passthrough      forbid_node for every node of the zone
  P-2 region_access_denied           avoid_node for every node of the zone; custom if it has none
  P-3 and compile errors             custom
  plan_json is not a TeamPlan        custom, rule llm_malformed (unreadable_plan_verdict)

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

The verification looks only at that pair of robots in that zone. It does not
guarantee that the reordered plan creates no new conflict elsewhere: on the
REJECT path the next round's check catches those; MODIFY (designed, not built)
must check the whole modified plan again before approving it.

rule_ids are mrs_common.reasons codes. risk_score is an ordinal level, not a
probability (SEVERITY). standard_refs stay empty: the team has not read the
standards' text yet, and EthicalVerdict only allows clauses that were read.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, NamedTuple

from mrs_common import reasons as R
from mrs_common.plan_schema import Constraint, TeamPlan
from pydantic import ValidationError

from ..plan import RegionKind
from .node_presence import presence
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
    constraints: list[Constraint] = []
    for finding in result.findings:
        for c in _constraints_for(finding, result):
            if c not in constraints:
                constraints.append(c)
    return VerdictContent(
        verdict=REJECT,
        rule_ids=tuple(sorted({f.reason for f in result.findings})),
        standard_refs=(),
        risk_score=max(SEVERITY[f.reason] for f in result.findings) / TOP_LEVEL,
        rationale=_rationale(result),
        constraints=tuple(constraints),
    )


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

def _constraints_for(f: Finding, result: TeamCheckResult) -> list[Constraint]:
    code = f.violation.code
    if code == "NODE_OCCUPIED":
        return _stop_constraints(f, result)
    if code in _CONFLICT_CODES:
        return _orderings(f, result)
    if code == "FINAL_STOP_IN_PASSTHROUGH":
        return _forbid_stops(f, result)
    if code == "REGION_ACCESS_DENIED":
        return _avoid_zone(f, result)
    if code == "MAX_DURATION_EXCEEDED":
        robot = f.violation.refs[0].robot_id
        return [Constraint(kind="custom", rule_id=f.reason, robot_id=robot,
                           text=f"{robot}: {_too_long(robot, result)}; make the plan shorter.")]
    # Compile errors: the message already says what is wrong, and with which task.
    return [Constraint(kind="custom", rule_id=f.reason, text=f.violation.message)]


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
    steps: dict[str, list[int]] = {}
    for ref in f.violation.refs:
        steps.setdefault(ref.robot_id, []).append(ref.step_index)
    return [_ordering(f, result, where, keeper=k, k_step=max(steps[k]), yielder=y, y_step=min(steps[y]))
            for y in f.must_fix for k in sorted(set(steps) - set(f.must_fix))]


def _ordering(f: Finding, result: TeamCheckResult, where: _Where, *,
              keeper: str, k_step: int, yielder: str, y_step: int) -> Constraint:
    k_task = result.compiled.origins[keeper][k_step].task_id    # takes the keeper out of the place
    y_task = result.compiled.origins[yielder][y_step].task_id   # takes the yielder into it
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


def _stop_constraints(f: Finding, result: TeamCheckResult) -> list[Constraint]:
    """S-4: one constraint for each (yielder, keeper) pair standing at the node."""
    steps: dict[str, list[int]] = {}
    for ref in f.violation.refs:
        steps.setdefault(ref.robot_id, []).append(ref.step_index)
    return [_stop_constraint(f, result, keeper=k, k_step=max(steps[k]), yielder=y, y_step=min(steps[y]))
            for y in f.must_fix for k in sorted(set(steps) - set(f.must_fix))]


def _stop_constraint(f: Finding, result: TeamCheckResult, *, keeper: str, k_step: int,
                     yielder: str, y_step: int) -> Constraint:
    """In order of preference: make the yielder come only after the keeper has left
    (verified, as for S-1); else, if the node is where the yielder's task ends, tell it to
    wait nearby first (or that the task cannot be done, if the keeper never leaves), since
    forbidding the node would make it give the task up; else forbid it to wait there."""
    node = f.violation.resource_id
    y = presence(result.compiled, result.team_plan, yielder, y_step, node)
    k = presence(result.compiled, result.team_plan, keeper, k_step, node)

    def make(kind: str, text: str, **fields: Any) -> Constraint:
        return Constraint(kind=kind, rule_id=f.reason, robot_id=yielder, text=text, **fields)

    if y.from_start:   # stands there from the start of its plan: it cannot come later
        return make("custom", f"{yielder} stands at {node} from the start of its plan, and {keeper} "
                              f"would stop there while it does. Have {yielder} leave {node} before "
                              f"{keeper} gets there.", node=node)
    if not k.stays:
        again = _reordered(result, before=k.leave_task, after=y.arrive_task)
        if again is not None and not _in_conflict(again, f, keeper, yielder):
            return make("order_before", f"{yielder}: start task {y.arrive_task} only after {keeper} has "
                                        f"finished task {k.leave_task}; both would stop at {node}, where "
                                        f"only one robot can stand.",
                        task_id=k.leave_task, value=y.arrive_task)
    if y.ends_task and k.stays:
        return make("custom", f"{yielder}: task {y.task} cannot be done with the plan as it is: its target "
                              f"{node} is where {keeper} stays until the end of its plan. Change where "
                              f"{keeper} ends, so that {node} is free when {yielder} gets there.",
                    task_id=y.task, node=node)
    if y.ends_task:
        return make("custom", f"{yielder}: {node} is where task {y.task} ends, but {keeper} is stopped there "
                              f"until it leaves with task {k.leave_task}. Wait at another node near {node} "
                              f"first, and go to {node} only after {keeper} has left it.",
                    task_id=y.task, node=node)
    until = ("stays there until the end of its plan" if k.stays
             else f"is stopped there until it leaves with task {k.leave_task}")
    return make("forbid_node", f"{yielder}: do not wait at {node}; {keeper} {until}.",
                task_id=y.task, node=node)


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
        robots = sorted({ref.robot_id for ref in refs})
        return f"{_join(robots)} would stop at {f.violation.resource_id} at the same time"
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
