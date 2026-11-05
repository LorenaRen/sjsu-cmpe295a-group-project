"""Every verdict a version of the checker gives on the benchmark's scenarios, as JSON lines, and
how two such files compare (pure Python).

One line per verdict, its keys sorted, for the timing scenarios (round null: the plan as
generated) and for every round of the repair runs (round 1, 2, ...: the fake planner's plans,
depends_on included). The line holds the verdict's decision fields as the gate publishes them,
constraints as their JSON. Two versions give the same verdicts exactly when the files are the
same byte for byte.
"""
from __future__ import annotations

import hashlib
import json
from itertools import zip_longest
from pathlib import Path
from typing import Any, Callable, Mapping

from mrs_common.plan_schema import constraints_to_json

from fleet_checker.adapters.team_check import check_team_plan
from fleet_checker.adapters.verdict import to_verdict

from . import config
from .perf import check_kwargs
from .repair import run_scenario
from .scenarios import Cell, cells, people_cells, scenarios_of


def verdict_line(scenario_id: str, round_: int | None, v: Any) -> str:
    return json.dumps({"id": scenario_id, "round": round_, "verdict": v.verdict, "rule_ids": list(v.rule_ids),
                       "standard_refs": list(v.standard_refs), "risk_score": v.risk_score, "rationale": v.rationale,
                       "constraints": constraints_to_json(v.constraints)}, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export(maps: Mapping[str, Any], profile: Any, path: Path, cells_filter: set[Cell] | None,
           log: Callable[[str], None]) -> dict[str, Any]:
    keep = lambda all_cells: [c for c in all_cells if cells_filter is None or c in cells_filter]   # noqa: E731
    timing = repair = 0
    with open(path, "w") as f:
        for cell in keep(cells(profile.robots) + people_cells(profile.people_robots)):
            for s in scenarios_of(maps, cell, profile.perf_scenarios):
                v = to_verdict(check_team_plan(s.plan, maps[cell[0]], s.starts, **check_kwargs(s)))
                f.write(verdict_line(s.scenario_id, None, v) + "\n")
                timing += 1
            log(f"verdicts of {'/'.join(map(str, cell))}: {timing} timing verdicts so far")
        for cell in keep(cells(profile.robots)):
            for s in scenarios_of(maps, cell, profile.repair_scenarios):
                def write(attempt: int, v: Any, sid: str = s.scenario_id) -> None:
                    nonlocal repair
                    f.write(verdict_line(sid, attempt + 1, v) + "\n")
                    repair += 1
                run_scenario(s, maps[cell[0]], config.REPAIR_MAX_ATTEMPTS, on_verdict=write)
            log(f"repair verdicts of {'/'.join(map(str, cell))}: {repair} so far")
    return {"timing_verdicts": timing, "repair_verdicts": repair, "lines": timing + repair, "sha256": _sha256(path)}


def diff(old: Path, new: Path) -> dict[str, Any]:
    """Line-by-line comparison of two verdict files: how many lines are identical, and the first that is not."""
    a, b = Path(old).read_text().splitlines(), Path(new).read_text().splitlines()
    identical, first = 0, None
    for i, (x, y) in enumerate(zip_longest(a, b), 1):
        if x == y:
            identical += 1
        elif first is None:
            first = {"line": i, "old": x, "new": y}
    return {"lines": max(len(a), len(b)), "old_lines": len(a), "new_lines": len(b), "identical": identical,
            "first_difference": first, "sha256": {"old": _sha256(Path(old)), "new": _sha256(Path(new))}}
