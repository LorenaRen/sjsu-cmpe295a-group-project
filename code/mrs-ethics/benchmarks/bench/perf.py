"""The timing benchmark: check_team_plan + to_verdict on each scenario (pure Python).

  total        check_team_plan, then to_verdict on its result
  compile      the compile_team_plan call of check_team_plan itself
  detection    the rest of check_team_plan: P-1, P-2, P-3, S-1, S-2 (with people), S-4, R-1,
               and who must change
  constraints  to_verdict: the constraints and the rationale, including the rechecks that
               verify an order_before. Each recheck compiles and checks a changed plan again;
               their number and time (and the compiling within them) are kept apart.

Instruments gets compile and the rechecks by wrapping compile_team_plan, as team_check calls
it, and TeamCheckResult.recheck while it is active; nothing in the checker changes. What the
wrapping costs is measured by overhead(): the same scenario, plain and instrumented, in turn.
Times are time.perf_counter_ns. The gate's settings are config.GATE; a fleet has no ledger.
"""
from __future__ import annotations

import gc
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import fleet_checker.adapters.team_check as team_check
from fleet_checker.adapters.sim_map import SimMap
from fleet_checker.adapters.team_check import check_team_plan
from fleet_checker.adapters.verdict import to_verdict
from fleet_checker.crowding import CrowdingLimit

from . import config
from .scenarios import Scenario

_NOW = time.perf_counter_ns
_RULES = {"s1": ("CAPACITY_EXCEEDED", "EDGE_SWAP"), "s2": ("HUMAN_CROWDING",), "s4": ("NODE_OCCUPIED",)}


@dataclass(frozen=True)
class Sample:
    scenario_id: str
    rep: int
    total_ns: int
    check_ns: int
    compile_ns: int
    detect_ns: int
    verdict_ns: int
    recheck_ns: int           # all rechecks of the verdict, compiling included
    recheck_compile_ns: int   # the compiling within them
    rechecks: int
    verdict: str
    findings: int
    constraints: int
    s1: int                   # findings by rule
    s2: int
    s4: int


@dataclass(frozen=True)
class OverheadPair:
    scenario_id: str
    rep: int
    plain_ns: int             # check + verdict without the instruments
    timed_ns: int             # the same, with them


def check_kwargs(scenario: Scenario) -> dict[str, Any]:
    """check_team_plan's settings: the gate's, and the scenario's people for S-2."""
    g = config.GATE
    return {"speed": g["speed"], "robot_length": g["robot_length"], "margin": g["margin"],
            "people": scenario.people,
            "crowding": CrowdingLimit(radius=g["crowding_radius"], max_robots=g["crowding_max_robots"])}


class Instruments:
    """While active, times compile_team_plan (by phase: the check's own, or within rechecks)
    and TeamCheckResult.recheck."""

    def __init__(self) -> None:
        self.reset()
        self._saved: tuple[Any, Any] | None = None

    def reset(self) -> None:
        self.phase = "check"
        self.compile_ns = {"check": 0, "verdict": 0}
        self.compiles = {"check": 0, "verdict": 0}
        self.recheck_ns = 0
        self.rechecks = 0

    def __enter__(self) -> Instruments:
        compile_team_plan, recheck = team_check.compile_team_plan, team_check.TeamCheckResult.recheck
        self._saved = (compile_team_plan, recheck)

        def timed_compile(*args: Any, **kwargs: Any):
            start = _NOW()
            out = compile_team_plan(*args, **kwargs)
            self.compile_ns[self.phase] += _NOW() - start
            self.compiles[self.phase] += 1
            return out

        def timed_recheck(result, team_plan):
            start = _NOW()
            out = recheck(result, team_plan)
            self.recheck_ns += _NOW() - start
            self.rechecks += 1
            return out

        team_check.compile_team_plan, team_check.TeamCheckResult.recheck = timed_compile, timed_recheck
        return self

    def __exit__(self, *exc: object) -> None:
        team_check.compile_team_plan, team_check.TeamCheckResult.recheck = self._saved


def measure(scenario: Scenario, sim_map: SimMap, inst: Instruments, rep: int) -> Sample:
    """One timed check + verdict of a scenario, with the instruments active."""
    kwargs = check_kwargs(scenario)
    inst.reset()
    t0 = _NOW()
    result = check_team_plan(scenario.plan, sim_map, scenario.starts, **kwargs)
    t1 = _NOW()
    inst.phase = "verdict"
    verdict = to_verdict(result)
    t2 = _NOW()
    inst.phase = "check"
    codes = [f.violation.code for f in result.findings]
    by_rule = {k: sum(c in rules for c in codes) for k, rules in _RULES.items()}
    return Sample(scenario.scenario_id, rep, total_ns=t2 - t0, check_ns=t1 - t0,
                  compile_ns=inst.compile_ns["check"], detect_ns=(t1 - t0) - inst.compile_ns["check"],
                  verdict_ns=t2 - t1, recheck_ns=inst.recheck_ns, recheck_compile_ns=inst.compile_ns["verdict"],
                  rechecks=inst.rechecks, verdict=verdict.verdict, findings=len(codes),
                  constraints=len(verdict.constraints), **by_rule)


def run_cell(maps: Mapping[str, SimMap], scenarios: Sequence[Scenario], repetitions: int,
             warmup: tuple[int, int], inst: Instruments) -> list[Sample]:
    """A cell's samples: after warmup (scenarios, repetitions) untimed runs, every scenario once per
    repetition, the scenarios in order within each repetition."""
    sim_map = maps[scenarios[0].map_name]
    gc.collect()
    for _ in range(warmup[1]):
        for s in scenarios[:warmup[0]]:
            measure(s, sim_map, inst, rep=-1)
    return [measure(s, sim_map, inst, rep) for rep in range(repetitions) for s in scenarios]


def overhead(maps: Mapping[str, SimMap], scenarios: Sequence[Scenario], repetitions: int) -> list[OverheadPair]:
    """Each scenario plain and instrumented, in turn; which goes first alternates."""
    sim_map = maps[scenarios[0].map_name]

    def plain(s: Scenario) -> int:
        kwargs = check_kwargs(s)
        start = _NOW()
        to_verdict(check_team_plan(s.plan, sim_map, s.starts, **kwargs))
        return _NOW() - start

    def timed(s: Scenario) -> int:
        with Instruments() as inst:
            return measure(s, sim_map, inst, rep=-1).total_ns

    for s in scenarios[:2]:   # warm-up
        plain(s), timed(s)
    gc.collect()
    pairs = []
    for rep in range(repetitions):
        for k, s in enumerate(scenarios):
            if (rep + k) % 2:
                t, p = timed(s), plain(s)
            else:
                p, t = plain(s), timed(s)
            pairs.append(OverheadPair(s.scenario_id, rep, p, t))
    return pairs
