"""The fake planner's loop (pure Python, no ROS): what it sends, when it stops, what happened.

  first()           attempt 0: the scenario's plan
  on_verdict(v)     the verdict for the attempt it waits for; any other verdict: None
                      APPROVE, MODIFY    done (the gate publishes the ValidatedPlan)
                      REJECT             scripted_repair.apply_constraints(), then
                        nothing changed              stuck: fall back now
                        no attempt left              exhausted: fall back
                        otherwise                    the next attempt
  timed_out()       no verdict came: done, without a fallback (the gate not answering is a
                    problem of the test setup, not a planning result)
  summary()         the outcome, every round, and what each constraint did

One transaction_id for every attempt; attempt 0 first, +1 after each REJECT (the conventions of
mrs_msgs' ProposedPlan and DialogueTurn). Rounds count from 1: round = attempt + 1.

Stuck falls back without using up the attempts: the fake planner is deterministic, so sending
the same plan again would only repeat it. (After M REJECTs in a row the gate's F-2 may change
who yields, but that is not planning.) Its reason is still planning_exhausted, the only code
mrs_common has for it; the event detail says stopped_early.

The fallback is mrs_common's hold_all_plan: the whole fleet holds where it starts (the spawn
slots, as the gate assumes; nothing has driven yet), reason planning_exhausted. The planner
publishes it on /mrs/validated_plan itself (source_verdict FALLBACK), not through the gate
(rules_v1.md, "Module 5 gate"), with the attempt of the last REJECT.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping

from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan, constraints_from_json, hold_all_plan

from .scripted_repair import CHANGED, NOT_APPLIED, NOTED, Graph, Outcome, apply_constraints

# The strings of EthicalVerdict.VERDICT_* (mrs_msgs needs a ROS build to import).
APPROVE = "APPROVE"
MODIFY = "MODIFY"
REJECT = "REJECT"

_RESULTS = (CHANGED, NOTED, NOT_APPLIED)
_RESULT_WORDS = {CHANGED: "changed", NOTED: "noted", NOT_APPLIED: "not applied"}
_STOPPED_EARLY = "no constraint of the REJECT could be applied"


@dataclass(frozen=True)
class GateVerdict:
    """The fields of an EthicalVerdict the loop reads."""
    transaction_id: str
    attempt: int
    verdict: str
    rule_ids: tuple[str, ...] = ()
    risk_score: float = 0.0
    rationale: str = ""
    constraints_json: str = "[]"
    latency_ms: float = 0.0           # the gate's own measurement


@dataclass(frozen=True)
class Proposal:
    """A plan to send as a ProposedPlan."""
    attempt: int
    plan: TeamPlan
    changes: tuple[str, ...]          # since the attempt before; () for attempt 0
    planning_ms: float                # the time it took to make (0 for attempt 0: the scenario's plan)

    @property
    def digest(self) -> str:
        """ProposedPlan.dialogue_digest: what made this attempt differ from the one before."""
        return "fake planner: " + ("; ".join(self.changes) if self.changes else "the scenario's plan")


@dataclass(frozen=True)
class Finished:
    outcome: str                                 # approved, modified, stuck, exhausted, no_verdict
    fallback: TeamPlan | None = None             # stuck and exhausted: the plan to publish as FALLBACK
    fallback_attempt: int | None = None          # the attempt the FALLBACK ValidatedPlan carries
    event_detail: dict[str, Any] | None = None   # detail of the planning_exhausted EpisodeEvent


class FakePlanner:
    def __init__(self, plan: TeamPlan, *, graph: Graph, start_nodes: Mapping[str, str], max_attempts: int,
                 transaction_id: str, episode_id: str = "", context: Mapping[str, Any] | None = None):
        """start_nodes: every robot of the fleet -> the node it starts at (for the fallback).
        context: what the summary starts with (scenario, description, map, ...)."""
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")
        self.transaction_id = transaction_id
        self.episode_id = episode_id
        self.max_attempts = max_attempts
        self._plan = plan
        self._graph = graph
        self._start_nodes = dict(start_nodes)
        self._context = dict(context or {})
        self._banned: dict[str, frozenset[str]] = {}
        self._sent: Proposal | None = None
        self._finished: Finished | None = None
        self._rounds: list[dict[str, Any]] = []
        self._outcomes: list[tuple[int, Outcome]] = []          # (round, outcome), every round
        self._targets: dict[str, list[str]] = {}                # task -> its targets, sent plans only
        self._inserted: list[str] = []

    @property
    def awaiting(self) -> int | None:
        """The attempt whose verdict the loop waits for, or None."""
        return self._sent.attempt if self._sent is not None and self._finished is None else None

    @property
    def finished(self) -> Finished | None:
        return self._finished

    @property
    def rounds(self) -> list[dict[str, Any]]:
        """One record for each verdict received: what it said and what each constraint did."""
        return self._rounds

    def first(self) -> Proposal:
        if self._sent is not None:
            raise RuntimeError("first() was called already")
        self._sent = Proposal(0, self._plan, (), 0.0)
        return self._sent

    def on_verdict(self, v: GateVerdict) -> Proposal | Finished | None:
        """The next plan to send, or how the loop ended; None for a verdict it is not waiting for."""
        if self.awaiting is None or (v.transaction_id, v.attempt) != (self.transaction_id, self.awaiting):
            return None
        if v.verdict not in (APPROVE, MODIFY, REJECT):
            raise ValueError(f"unknown verdict {v.verdict!r}")
        record: dict[str, Any] = {"round": v.attempt + 1, "attempt": v.attempt, "verdict": v.verdict,
                                  "rule_ids": list(v.rule_ids), "risk_score": v.risk_score,
                                  "gate_latency_ms": v.latency_ms, "rationale": v.rationale}
        if v.verdict != REJECT:
            outcome = "approved" if v.verdict == APPROVE else "modified"
            self._rounds.append({**record, "constraints": [], "changes": [], "then": outcome})
            return self._finish(Finished(outcome))

        start = time.perf_counter()
        repair = apply_constraints(self._sent.plan, constraints_from_json(v.constraints_json), self._graph,
                                   self._banned, self._start_nodes)
        planning_ms = (time.perf_counter() - start) * 1e3
        self._outcomes.extend((v.attempt + 1, o) for o in repair.outcomes)
        if not repair.changes:
            then, finished = "stuck: nothing to change; FALLBACK", self._fallback(v.attempt, stuck=True)
        elif v.attempt + 1 >= self.max_attempts:
            then, finished = ("exhausted: no attempt left, these changes were not sent; FALLBACK",
                              self._fallback(v.attempt, stuck=False))
        else:
            then, finished = f"attempt {v.attempt + 1} sent", None
        self._rounds.append({**record, "constraints": [o.as_record() for o in repair.outcomes],
                             "changes": list(repair.changes), "then": then})
        if finished is not None:
            return self._finish(finished)

        proposal = Proposal(v.attempt + 1, repair.plan, repair.changes, planning_ms)
        self._track(self._sent.plan, proposal)
        self._banned = repair.banned
        self._sent = proposal
        return proposal

    def timed_out(self) -> Finished:
        return self._finished or self._finish(Finished("no_verdict"))

    # ---- summary ----------------------------------------------------------------------------
    def summary(self) -> dict[str, Any]:
        f = self._finished
        counts: dict[tuple[str, str], dict[str, Any]] = {}
        for _, o in self._outcomes:
            key = (o.constraint.kind, o.constraint.rule_id)
            entry = counts.setdefault(key, {"kind": key[0], "rule_id": key[1], "received": 0,
                                            **{r: 0 for r in _RESULTS}})
            entry["received"] += 1
            entry[o.result] += 1
        return {
            **self._context,
            "fleet": list(self._start_nodes),
            "max_attempts": self.max_attempts,
            "transaction_id": self.transaction_id,
            "episode_id": self.episode_id,
            "outcome": f.outcome if f else None,
            "rounds": len(self._rounds),
            "passed_in_round": len(self._rounds) if f and f.outcome in ("approved", "modified") else None,
            "constraints": [counts[k] for k in sorted(counts)],
            "not_applied": [{"round": n, "kind": o.constraint.kind, "rule_id": o.constraint.rule_id,
                             "robot_id": o.constraint.robot_id, "why_not": o.why_not, "text": o.constraint.text}
                            for n, o in self._outcomes if o.result == NOT_APPLIED],
            "targets_changed": {task_id: list(chain) for task_id, chain in self._targets.items()},
            "tasks_inserted": list(self._inserted),
            "fallback": None if f is None or f.fallback is None else {
                "attempt": f.fallback_attempt, "reason": R.PLANNING_EXHAUSTED, "hold": dict(self._start_nodes)},
            "round_log": self._rounds,
        }

    def summary_text(self) -> list[str]:
        """The summary in words, a line each, for the terminal."""
        s = self.summary()
        lines = [f"fake planner: scenario {s.get('scenario', '-')} on map {s.get('map', '-')}, "
                 f"fleet {', '.join(s['fleet'])}, at most {s['max_attempts']} attempts"]
        for r in self._rounds:
            lines.append(f"round {r['round']} (attempt {r['attempt']}): {r['verdict']}"
                         + (f" {', '.join(r['rule_ids'])}" if r["rule_ids"] else ""))
            for c in r["constraints"]:
                where = f" {c['node']}" if c.get("node") else ""
                who = f" for {c['robot_id']}" if c.get("robot_id") else ""
                head = f"  {c['kind']}{where}{who} ({c['rule_id']}): "
                if c["result"] == CHANGED:
                    lines.append(head + f"changed ({c['change']})")
                elif c["result"] == NOTED:
                    lines.append(head + f"noted (kept out of {c.get('robot_id')}'s tasks from now on)")
                else:
                    lines += [head + f"not applied: {c['why_not']}", f"    {c['text']}"]
        lines.append("outcome: " + self._outcome_words(s))
        if s["tasks_inserted"]:
            lines.append("tasks inserted: " + "; ".join(s["tasks_inserted"]))
        if s["constraints"]:
            lines.append("constraints: " + "; ".join(
                f"{c['kind']} {c['rule_id']} {c['received']} received"
                + "".join(f", {c[r]} {_RESULT_WORDS[r]}" for r in _RESULTS if c[r])
                for c in s["constraints"]))
        return lines

    # ---- internals --------------------------------------------------------------------------
    def _finish(self, finished: Finished) -> Finished:
        self._finished = finished
        return finished

    def _fallback(self, attempt: int, *, stuck: bool) -> Finished:
        fleet = list(self._start_nodes)
        detail = R.detail(R.PLANNING_EXHAUSTED, transaction_id=self.transaction_id, attempts=attempt + 1,
                          max_attempts=self.max_attempts)
        if stuck:
            detail["stopped_early"] = _STOPPED_EARLY
        return Finished("stuck" if stuck else "exhausted",
                        hold_all_plan(fleet, self._start_nodes, R.PLANNING_EXHAUSTED), attempt, detail)

    def _track(self, before: TeamPlan, proposal: Proposal) -> None:
        """Remember the targets that changed and the tasks inserted by a plan about to be sent."""
        old = {t.task_id: t.target for a in before.assignments for t in a.tasks}
        for a in proposal.plan.assignments:
            for t in a.tasks:
                if t.task_id not in old:
                    self._inserted += [c for c in proposal.changes if c.startswith(f"{t.task_id}:")]
                elif t.target != old[t.task_id]:
                    self._targets.setdefault(t.task_id, [old[t.task_id]]).append(t.target)

    def _outcome_words(self, s: dict[str, Any]) -> str:
        f = self._finished
        if f is None:
            return "not finished"
        holds = ", ".join(f"{robot} at {node}" for robot, node in self._start_nodes.items())
        fallback = f"FALLBACK: every robot holds where it starts ({holds})"
        if f.outcome in ("approved", "modified"):
            words = "approved in" if f.outcome == "approved" else "modified by the gate in"
            changed = "; ".join(f"{t} {' -> '.join(chain)}" for t, chain in s["targets_changed"].items())
            return (f"{words} round {s['passed_in_round']}, "
                    + (f"with changed task targets: {changed}" if changed else "no task target changed"))
        if f.outcome == "stuck":
            return f"stuck in round {s['rounds']}: {_STOPPED_EARLY}; {fallback}"
        if f.outcome == "exhausted":
            return f"exhausted: rejected in all {s['rounds']} attempts; {fallback}"
        return f"no verdict for attempt {self._sent.attempt}"
