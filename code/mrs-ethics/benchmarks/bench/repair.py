"""The repair benchmark: the fake planner against the gate's session, in process (pure Python).

Each scenario gets a new GateSession (the gate's settings, config.GATE; the scenario's start
nodes; its people, if any) and a FakePlanner with its plan, the map's graph and
REPAIR_MAX_ATTEMPTS. They are wired as the two nodes are, minus ROS (as in mrs_ethical_gate's
test conftest): every attempt goes through decide(), and each verdict goes back to the fake
planner through constraints_json.

What it measures is how far the gate's constraints alone get a scripted planner that applies
them mechanically (dev_nodes/scripted_repair.py), not how often an LLM planner succeeds.

Each constraint is also counted by the situation it answers (VerdictContent.cases, e.g. S-4's
"yielder_waits"), paired with what the fake planner did with it. A checker from before
VerdictContent.cases has none: its constraints count as "-".

Outcomes, one per scenario:
  first_round      approved in round 1: the plan had no finding
  kept_targets     approved in a later round, every task still going where it went: the fake
                   planner only added depends_on or inserted waits
  changed_targets  approved in a later round, with at least one task's target moved
  stuck            a REJECT the fake planner could change nothing for, then FALLBACK
  exhausted        REPAIR_MAX_ATTEMPTS plans sent and the last one rejected, then FALLBACK
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Sequence

from mrs_common.plan_schema import constraints_to_json, plan_to_json

from fleet_checker.adapters.sim_map import SimMap
from fleet_checker.crowding import CrowdingLimit
from mrs_ethical_gate.dev_nodes.replan_loop import FakePlanner, GateVerdict, Proposal
from mrs_ethical_gate.session import GateSession

from . import config
from .scenarios import Scenario
from .stats import wilson

OUTCOMES = ("first_round", "kept_targets", "changed_targets", "stuck", "exhausted")
RESULTS = ("changed", "noted", "not_applied")
_RULES = {"s1": ("CAPACITY_EXCEEDED", "EDGE_SWAP"), "s2": ("HUMAN_CROWDING",), "s4": ("NODE_OCCUPIED",)}


@dataclass(frozen=True)
class RepairRun:
    scenario_id: str
    outcome: str
    rounds: int
    passed_in_round: int | None
    first_findings: int                     # findings of round 1
    first_s1: int
    first_s2: int
    first_s4: int
    targets_changed: int                    # tasks whose target the fake planner moved
    tasks_inserted: int
    constraints: tuple[dict[str, Any], ...]  # the fake planner's counts by (kind, rule_id)
    cases: tuple[dict[str, Any], ...] = ()    # the same counts by (kind, rule_id, case)


def classify(summary: dict[str, Any]) -> str:
    outcome = summary["outcome"]
    if outcome == "approved":
        if summary["rounds"] == 1:
            return "first_round"
        return "changed_targets" if summary["targets_changed"] else "kept_targets"
    if outcome in ("stuck", "exhausted"):
        return outcome
    raise ValueError(f"unexpected outcome {outcome!r}")   # modified, no_verdict: not in process


def run_scenario(scenario: Scenario, sim_map: SimMap, max_attempts: int,
                 on_verdict: Callable[[int, Any], None] | None = None) -> RepairRun:
    """One scenario through the loop; on_verdict(attempt, verdict content) sees every verdict."""
    g = config.GATE
    gate = GateSession(sim_map, scenario.starts, speed=g["speed"], robot_length=g["robot_length"],
                       margin=g["margin"], people=scenario.people, starvation_threshold=config.STARVATION_THRESHOLD,
                       **config.every_rule(GateSession, fairness_ledger=True),
                       crowding=CrowdingLimit(radius=g["crowding_radius"], max_robots=g["crowding_max_robots"]))
    txn, episode = f"bench/{scenario.scenario_id}", "bench"
    planner = FakePlanner(scenario.plan, graph=sim_map.neighbors, start_nodes=scenario.starts,
                          max_attempts=max_attempts, transaction_id=txn, episode_id=episode)
    step, first, cases = planner.first(), None, []
    while isinstance(step, Proposal):
        d = gate.decide(plan_to_json(step.plan), transaction_id=txn, episode_id=episode, attempt=step.attempt,
                        now=0.0)
        if first is None:
            first = [f.violation.code for f in d.result.findings]
        v = d.verdict
        cases.append(getattr(v, "cases", None))
        if on_verdict is not None:
            on_verdict(step.attempt, v)
        step = planner.on_verdict(GateVerdict(
            transaction_id=txn, attempt=step.attempt, verdict=v.verdict, rule_ids=v.rule_ids,
            risk_score=v.risk_score, rationale=v.rationale, constraints_json=constraints_to_json(v.constraints)))
    s = planner.summary()
    by_rule = {f"first_{k}": sum(c in rules for c in first) for k, rules in _RULES.items()}
    return RepairRun(scenario.scenario_id, classify(s), s["rounds"], s["passed_in_round"], len(first),
                     targets_changed=len(s["targets_changed"]), tasks_inserted=len(s["tasks_inserted"]),
                     constraints=tuple(s["constraints"]), cases=case_counts(planner.rounds, cases), **by_rule)


def case_counts(rounds: Sequence[dict[str, Any]],
                verdict_cases: Sequence[Sequence[str] | None]) -> tuple[dict[str, Any], ...]:
    """What the fake planner did with the constraints of each situation: its round records (one
    per verdict, each constraint's outcome in the constraints' order) paired with the verdicts'
    cases (None for a checker without them)."""
    counts: dict[tuple[str, str, str], Counter] = {}
    for record, cases in zip(rounds, verdict_cases, strict=True):
        done = record["constraints"]
        cases = ["-"] * len(done) if cases is None else list(cases)
        if len(cases) != len(done):
            raise ValueError(f"round {record.get('round')}: {len(done)} constraints, {len(cases)} cases")
        for c, case in zip(done, cases):
            counts.setdefault((c["kind"], c["rule_id"], case), Counter()).update({"received": 1, c["result"]: 1})
    return tuple({"kind": kind, "rule_id": rule, "case": case,
                  **{x: n.get(x, 0) for x in ("received",) + RESULTS}}
                 for (kind, rule, case), n in sorted(counts.items()))


def tally(runs: Sequence[RepairRun]) -> dict[str, Any]:
    """What a set of runs adds up to: outcome counts and shares with Wilson 95 % intervals, over
    all runs and over those with a conflict in round 1; the round each repaired plan passed in;
    and what the fake planner did with each kind of constraint (and each kind and rule, and each
    kind, rule and situation)."""
    conflicted = [r for r in runs if r.first_findings]
    by_kind: dict[str, Counter] = {}
    by_rule: dict[tuple[str, str], Counter] = {}
    by_case: dict[tuple[str, str, str], Counter] = {}
    for r in runs:
        for c in r.constraints:
            for key, table in ((c["kind"], by_kind), ((c["kind"], c["rule_id"]), by_rule)):
                table.setdefault(key, Counter()).update({x: c[x] for x in ("received",) + RESULTS})
        for c in r.cases:
            by_case.setdefault((c["kind"], c["rule_id"], c["case"]), Counter()).update(
                {x: c[x] for x in ("received",) + RESULTS})
    return {
        "scenarios": len(runs),
        "outcomes": _shares(runs, OUTCOMES),
        "with_conflict": {"scenarios": len(conflicted), "outcomes": _shares(conflicted, OUTCOMES[1:])},
        "passed_in_round": dict(sorted(Counter(r.passed_in_round for r in runs if r.passed_in_round).items())),
        "by_kind": {k: dict(v) for k, v in sorted(by_kind.items())},
        "by_rule": {k: dict(v) for k, v in sorted(by_rule.items())},
        "by_case": {k: dict(v) for k, v in sorted(by_case.items())},
    }


def _shares(runs: Sequence[RepairRun], outcomes: Sequence[str]) -> dict[str, dict[str, Any]]:
    if not runs:
        return {}
    counts = Counter(r.outcome for r in runs)
    return {o: {"count": counts[o], "share": counts[o] / len(runs), "interval": wilson(counts[o], len(runs))}
            for o in outcomes}
