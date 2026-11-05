"""Why did incremental rechecks gain less at 32 robots than at 16: the quadratic lookup of R-1's
yielders? The numbers behind answer.md, printed as markdown tables.

    .venv/bin/python code/mrs-ethics/benchmarks/reports/2026-11-04_Mac14-15_r1_compare/answer.py

Times come from four committed comparisons on bench_grid, at 8, 16 and 32 robots. Each cell is
taken from the run of its session that measured it at a quiet load (the reruns, where there is one):
  - the first session, bb46d18 (before incremental rechecks) -> 120a928 (incremental rechecks):
    ../2026-11-04_Mac14-15_compare_rerun (8 and 16 robots), ../2026-11-04_Mac14-15_compare (32);
  - this session, 969d5a8 (main, incremental rechecks) -> f88f03b (the lookup in one pass):
    ../2026-11-04_Mac14-15_r1_compare_rerun (8 and 16 robots), ./ (32).
969d5a8's check and verdict code is 120a928's: what main gained since (PR #14, mrs_common's message
conversion and one reason code) is not on the timed path.
The counts are computed again here, by running the checker on the same scenarios: how many
comparisons the old lookup made, from the conflicts and yielders R-1 returns (which the lookup
does not change).
"""
from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))   # benchmarks/, for bench (which puts the checker on the path)

CELLS = [("bench_grid", n, d, 0) for n in (8, 16, 32) for d in ("low", "medium", "high")]


def rows(path: Path) -> dict[tuple[str, int, str, int], dict[str, str]]:
    with open(path, newline="") as f:
        return {(r["map"], int(r["robots"]), r["density"], int(r["people"])): r for r in csv.DictReader(f)}


def table(header: list[str], body: list[list[str]]) -> str:
    return "\n".join(["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
                     + ["| " + " | ".join(r) + " |" for r in body])


def times() -> str:
    def quiet(rerun: str, run: Path) -> dict:
        return rows(run / "compare.csv") | rows(HERE.parent / rerun / "compare.csv")
    first = quiet("2026-11-04_Mac14-15_compare_rerun", HERE.parent / "2026-11-04_Mac14-15_compare")
    this = quiet("2026-11-04_Mac14-15_r1_compare_rerun", HERE)
    body = []
    for cell in CELLS:
        a, b = first[cell], this[cell]
        t_bb, t_inc1 = float(a["old_median_ms"]), float(a["new_median_ms"])
        t_main, t_fix = float(b["old_median_ms"]), float(b["new_median_ms"])
        lookup = t_main - t_fix
        body.append([f"{cell[1]} {cell[2]}", f"{t_main:.4g}", f"{t_fix:.4g}", f"{t_main / t_fix:.3f}",
                     f"{lookup / t_main:.0%}", f"{t_main / t_inc1 - 1:+.1%}",
                     f"{t_bb / t_inc1:.2f}", f"{(t_bb - lookup) / t_fix:.2f}", f"{t_bb / t_fix:.2f}"])
    return table(["robots, density", "main (ms)", "one pass (ms)", "speedup", "lookup's share of main",
                  "drift: main here vs 120a928 then", "incremental speedup, measured", "same, neither with the lookup",
                  "bb46d18 -> one pass"], body)


def counts() -> str:
    from bench.maps import load_maps            # first: bench puts the checker on the path
    import fleet_checker.adapters.compiler as compiler
    import fleet_checker.adapters.team_check as team_check
    from bench.perf import check_kwargs
    from bench.scenarios import scenarios_of
    from fleet_checker.adapters.team_check import check_team_plan
    from fleet_checker.adapters.verdict import to_verdict

    seen: list[tuple[int, int, int]] = []   # per check or recheck: conflicts, listed entries, old comparisons
    rebuilt: list[float] = []               # per incremental recompile: the share of robots rebuilt
    inner, recompile = team_check.check_cross_plan_conflicts, compiler._recompile

    def recompiling(team_plan, base, *args, **kwargs):
        again = recompile(team_plan, base, *args, **kwargs)
        if again is not None:
            rebuilt.append(sum(again.plans[r] is not base.plans[r] for r in base.plans) / len(base.plans))
        return again

    def counting(*args, **kwargs):
        conflicts, yielders = inner(*args, **kwargs)
        where = {r: {id(y): i for i, y in enumerate(vs)} for r, vs in yielders.items()}
        # the old lookup: for each conflict, every robot's list, scanned until the conflict is found
        compared = sum(where[r].get(id(v), len(vs) - 1) + 1 for v in conflicts for r, vs in yielders.items())
        seen.append((len(conflicts), sum(map(len, yielders.values())), compared))
        return conflicts, yielders

    maps = load_maps()
    body = []
    team_check.check_cross_plan_conflicts, compiler._recompile = counting, recompiling
    try:
        for cell in CELLS:
            per_scenario, rebuilt_shares = [], []
            for s in scenarios_of(maps, cell, 200):
                seen.clear()
                rebuilt.clear()
                to_verdict(check_team_plan(s.plan, maps[s.map_name], s.starts, **check_kwargs(s)))
                per_scenario.append((len(seen), statistics.median(c for c, _, _ in seen),
                                     sum(e for _, e, _ in seen), sum(k for _, _, k in seen)))
                rebuilt_shares += rebuilt
            med = [statistics.median(x[i] for x in per_scenario) for i in range(4)]
            share = f"{statistics.mean(rebuilt_shares):.0%}" if rebuilt_shares else "-"
            body.append([f"{cell[1]} {cell[2]}", f"{med[0]:g}", f"{med[1]:g}", f"{med[3]:,.0f}", f"{med[2]:,.0f}",
                         share])
    finally:
        team_check.check_cross_plan_conflicts, compiler._recompile = inner, recompile
    return table(["robots, density", "checks per scenario (1 + rechecks)", "conflicts per check",
                  "old lookup: comparisons per scenario", "one pass: entries read per scenario",
                  "robots a recheck rebuilds (mean share)"], body)


if __name__ == "__main__":
    print("## Times (medians of 200 scenarios x 5 repetitions)\n")
    print(times())
    print("\n## Work in the lookup (medians over the 200 scenarios of each cell)\n")
    print(counts())
