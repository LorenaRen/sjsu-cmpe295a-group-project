"""S-4's custom constraints: the repair benchmark before and after (writes compare.md here).

    .venv/bin/python code/mrs-ethics/benchmarks/reports/2026-11-05_Mac14-15_s4/compare_repair.py

Reads the three repair runs beside it, each `run.py --only repair` on one commit of
haoran/s4-actionable, all on the same 1350 scenarios:
  1_baseline       d64457f: the checker of main (c93795c), with VerdictContent.cases
  2_fake_planner   14e0480: + the fake planner waits away from where a robot starts, and never
                   moves a task to where another robot ends
  3_checker        65acde1: + S-4's forbid_node for a yielder waiting where it starts, a keeper
                   waiting in the yielder's way, and a keeper staying where the yielder's task ends
Every number in compare.md is computed here from those runs' CSV files.
"""
from __future__ import annotations

import csv
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = [("1_baseline", "baseline"), ("2_fake_planner", "+ fake planner"), ("3_checker", "+ checker")]
OUTCOMES = [("first_round", "approved in round 1"), ("kept_targets", "repaired, targets kept"),
            ("changed_targets", "repaired, a target moved"), ("stuck", "stuck"), ("exhausted", "out of attempts")]


def rows(run: str, name: str) -> list[dict[str, str]]:
    with open(HERE / run / name, newline="") as f:
        return list(csv.DictReader(f))


def table(header: list[str], body: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in body]


def share(r: dict[str, str], key: str) -> str:
    return (f"{float(r[key + '_share']):.1%} [{float(r[key + '_lo']):.1%}–{float(r[key + '_hi']):.1%}] "
            f"({r[key + '_count']})")


def summary_row(run: str, map_name: str = "*", robots: str = "*") -> dict[str, str]:
    [r] = [r for r in rows(run, "repair_summary.csv")
           if (r["map"], r["robots"], r["density"]) == (map_name, robots, "*")]
    return r


def main() -> None:
    out = ["# S-4's custom constraints: the repair benchmark before and after", "",
           "Same 1350 scenarios in every run (manifest-checked), at most 5 attempts. Share [Wilson 95 %] (count). "
           "Computed by `compare_repair.py` from the runs' CSV files; each run's own report is beside it.", ""]
    for title, prefix, keys in (("## All scenarios", "", OUTCOMES), ("## Scenarios with a conflict in round 1", "wc_",
                                                                    OUTCOMES[1:])):
        out += [title, ""]
        body = [[label] + [share(summary_row(run), prefix + key) for run, _ in RUNS] for key, label in keys]
        n = [summary_row(run)["scenarios" if not prefix else "with_conflict"] for run, _ in RUNS]
        out += table(["outcome"] + [f"{label} (n = {k})" for (_, label), k in zip(RUNS, n)], body) + [""]

    out += ["## Stuck, by fleet size (bench_grid)", ""]
    body = []
    for robots in ("2", "4", "8", "16", "32"):
        body.append([robots] + [share(summary_row(run, "bench_grid", robots), "stuck") for run, _ in RUNS])
    out += table(["robots"] + [label for _, label in RUNS], body) + [""]

    out += ["## Rounds", "", "Mean and median rounds of the repaired scenarios (kept or changed targets).", ""]
    body = []
    for run, label in RUNS:
        rounds = [int(r["rounds"]) for r in rows(run, "repair_scenarios.csv")
                  if r["outcome"] in ("kept_targets", "changed_targets")]
        body.append([label, str(len(rounds)), f"{statistics.mean(rounds):.2f}", f"{statistics.median(rounds):g}"])
    out += table(["run", "repaired", "mean rounds", "median rounds"], body) + [""]

    out += ["## Constraints by kind", "", "Over all scenarios: received (changed / noted / not applied).", ""]
    kinds = sorted({(c["kind"], c["rule_id"]) for run, _ in RUNS for c in rows(run, "repair_constraints.csv")
                    if c["map"] == "*" and c["rule_id"] != "*"})
    body = []
    for kind, rule in kinds:
        cells = []
        for run, _ in RUNS:
            [c] = [c for c in rows(run, "repair_constraints.csv")
                   if (c["map"], c["kind"], c["rule_id"]) == ("*", kind, rule)] or [None]
            cells.append("-" if c is None else f"{c['received']} ({c['changed']} / {c['noted']} / {c['not_applied']})")
        body.append([kind, rule] + cells)
    out += table(["kind", "rule", *[label for _, label in RUNS]], body) + [""]

    out += ["## S-4's constraints by situation", "",
            "Over all scenarios: received (changed / noted / not applied), by `VerdictContent.cases` and kind.", ""]
    cases = sorted({(c["case"], c["kind"]) for run, _ in RUNS for c in rows(run, "repair_cases.csv")
                    if c["map"] == "*" and c["rule_id"] == "node_occupied"})
    body = []
    for case, kind in cases:
        cells = []
        for run, _ in RUNS:
            [c] = [c for c in rows(run, "repair_cases.csv") if (c["map"], c["rule_id"], c["case"], c["kind"])
                   == ("*", "node_occupied", case, kind)] or [None]
            cells.append("-" if c is None else f"{c['received']} ({c['changed']} / {c['noted']} / {c['not_applied']})")
        body.append([f"`{case}`", kind] + cells)
    out += table(["situation", "kind", *[label for _, label in RUNS]], body) + [""]

    (HERE / "compare.md").write_text("\n".join(out))
    print("\n".join(out))


if __name__ == "__main__":
    main()
