"""The numbers of a benchmark run, as CSV rows and as the sentences of report.md (pure Python).

Every number in report.md is computed here from the samples and runs that the CSV files next
to it hold; the text around the numbers is fixed. Times are in ms, rounded to 0.1 us; shares
are of the summed time of all samples of a cell (or of all cells pooled), so they add up.
"""
from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .perf import OverheadPair, Sample
from .repair import OUTCOMES, RESULTS, RepairRun, tally
from .scenarios import Cell, Scenario
from .stats import percentile

PARTS = ("compile", "detection", "rechecks", "other constraint work")   # the bottleneck's candidates
_KINDS = ("order_before", "forbid_node", "avoid_node", "custom")


def ms(ns: float) -> float:
    return round(ns / 1e6, 4)


def _cell_fields(cell: Cell) -> dict[str, Any]:
    map_name, robots, density, people = cell
    return {"map": map_name, "robots": robots, "density": density, "people": people}


def _mean(values: Iterable[float]) -> float:
    values = list(values)
    return round(sum(values) / len(values), 4)


# ---- timing --------------------------------------------------------------------------------

def perf_cell_row(cell: Cell, samples: Sequence[Sample]) -> dict[str, Any]:
    row = {**_cell_fields(cell), "scenarios": len({s.scenario_id for s in samples}), "samples": len(samples)}
    for name in ("findings", "s1", "s2", "s4", "constraints", "rechecks"):
        row[f"{name}_mean"] = _mean(getattr(s, name) for s in samples)
    row["approve_share"] = _mean(s.verdict == "APPROVE" for s in samples)
    for part, attr in (("total", "total_ns"), ("compile", "compile_ns"), ("detect", "detect_ns"),
                       ("constraints", "verdict_ns"), ("recheck", "recheck_ns")):
        values = [getattr(s, attr) for s in samples]
        for p, label in ((50, "median"), (95, "p95"), (99, "p99")):
            row[f"{part}_{label}_ms"] = ms(percentile(values, p))
    total = sum(s.total_ns for s in samples)
    recheck = sum(s.recheck_ns for s in samples)
    row["share_compile"] = round(sum(s.compile_ns for s in samples) / total, 4)
    row["share_detect"] = round(sum(s.detect_ns for s in samples) / total, 4)
    row["share_rechecks"] = round(recheck / total, 4)
    row["share_constraints_other"] = round((sum(s.verdict_ns for s in samples) - recheck) / total, 4)
    rechecks = sum(s.rechecks for s in samples)
    row["ms_per_recheck"] = ms(recheck / rechecks) if rechecks else ""
    row["total_sum_ms"], row["recheck_sum_ms"], row["rechecks_sum"] = ms(total), ms(recheck), rechecks
    return row


def perf_scenario_rows(cell: Cell, samples: Sequence[Sample], scenarios: Sequence[Scenario]) -> list[dict[str, Any]]:
    """One row per scenario: its counts and the median of its repetitions of each part."""
    by_id: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        by_id[s.scenario_id].append(s)
    rows = []
    for sc in scenarios:
        xs, first = by_id[sc.scenario_id], by_id[sc.scenario_id][0]
        row = {**_cell_fields(cell), "index": sc.index, "scenario_id": sc.scenario_id, "crossings": sc.crossings,
               **{k: getattr(first, k) for k in ("verdict", "findings", "s1", "s2", "s4", "constraints", "rechecks")}}
        for part, attr in (("total", "total_ns"), ("compile", "compile_ns"), ("detect", "detect_ns"),
                           ("constraints", "verdict_ns"), ("recheck", "recheck_ns")):
            row[f"{part}_median_ms"] = ms(percentile([getattr(x, attr) for x in xs], 50))
        rows.append(row)
    return rows


def people_rows(samples: Mapping[Cell, Sequence[Sample]], scenarios: Mapping[Cell, Sequence[Scenario]],
                people: int) -> list[dict[str, Any]]:
    """The S-2 series: each cell with `people` people against the same scenarios without them.
    The extra time is per scenario (the median of its repetitions with people, less without),
    then over the scenarios."""
    rows = []
    for cell in [c for c in samples if c[3] == people]:
        base = cell[:3] + (0,)

        def medians(c: Cell, attr: str) -> dict[int, float]:
            index = {sc.scenario_id: sc.index for sc in scenarios[c]}
            by_index: dict[int, list[float]] = defaultdict(list)
            for s in samples[c]:
                by_index[index[s.scenario_id]].append(getattr(s, attr))
            return {i: percentile(v, 50) for i, v in by_index.items()}

        extra = {}
        for part, attr in (("total", "total_ns"), ("detect", "detect_ns"), ("constraints", "verdict_ns")):
            with_, without = medians(cell, attr), medians(base, attr)
            extra[part] = [with_[i] - without[i] for i in sorted(with_)]
        rows.append({**_cell_fields(cell), "scenarios": len(extra["total"]),
                     "s2_findings_mean": _mean(s.s2 for s in samples[cell]),
                     "total_median_ms_without": ms(percentile([s.total_ns for s in samples[base]], 50)),
                     "total_median_ms_with": ms(percentile([s.total_ns for s in samples[cell]], 50)),
                     "extra_median_ms": ms(percentile(extra["total"], 50)),
                     "extra_p95_ms": ms(percentile(extra["total"], 95)),
                     "detect_extra_median_ms": ms(percentile(extra["detect"], 50)),
                     "constraints_extra_median_ms": ms(percentile(extra["constraints"], 50)),
                     "rechecks_mean_without": _mean(s.rechecks for s in samples[base]),
                     "rechecks_mean_with": _mean(s.rechecks for s in samples[cell])})
    return rows


def overhead_row(cell: Cell, pairs: Sequence[OverheadPair]) -> dict[str, Any]:
    diffs = [p.timed_ns - p.plain_ns for p in pairs]
    plain = percentile([p.plain_ns for p in pairs], 50)
    return {**_cell_fields(cell), "pairs": len(pairs), "plain_median_ms": ms(plain),
            "timed_median_ms": ms(percentile([p.timed_ns for p in pairs], 50)),
            "diff_median_ms": ms(percentile(diffs, 50)), "diff_p95_ms": ms(percentile(diffs, 95)),
            "diff_share": round(percentile(diffs, 50) / plain, 4)}


def _pooled_shares(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    total = sum(r["total_sum_ms"] for r in rows)
    keys = {"compile": "share_compile", "detection": "share_detect", "rechecks": "share_rechecks",
            "other constraint work": "share_constraints_other"}
    return {part: sum(r[k] * r["total_sum_ms"] for r in rows) / total for part, k in keys.items()}


def bottleneck_lines(rows: Sequence[Mapping[str, Any]], map_name: str) -> list[str]:
    """For each fleet size on a map (its densities pooled): the part that takes the most time."""
    lines = []
    for n in sorted({r["robots"] for r in rows if r["map"] == map_name}):
        shares = _pooled_shares([r for r in rows if r["map"] == map_name and r["robots"] == n and not r["people"]])
        largest = max(PARTS, key=lambda p: shares[p])
        lines.append(f"{n} robots: {largest} take{'s' if largest != 'rechecks' else ''} {shares[largest]:.0%} of "
                     f"the time (" + ", ".join(f"{p} {shares[p]:.0%}" for p in PARTS) + ")")
    return lines


def growth_lines(rows: Sequence[Mapping[str, Any]], map_name: str) -> tuple[list[dict[str, Any]], list[str]]:
    """How rechecks grow with the fleet on a map (densities pooled): per fleet size, the mean
    rechecks per check, the mean findings and the time per recheck; then the factor per
    doubling and the slope of a least-squares fit of log(mean rechecks) on log(robots), over
    the fleet sizes with any rechecks."""
    table = []
    for n in sorted({r["robots"] for r in rows if r["map"] == map_name}):
        cell_rows = [r for r in rows if r["map"] == map_name and r["robots"] == n and not r["people"]]
        samples = sum(r["samples"] for r in cell_rows)
        rechecks = sum(r["rechecks_sum"] for r in cell_rows)
        table.append({"robots": n, "rechecks_mean": round(rechecks / samples, 4),
                      "findings_mean": round(sum(r["findings_mean"] * r["samples"] for r in cell_rows) / samples, 4),
                      "ms_per_recheck": ms(sum(r["recheck_sum_ms"] for r in cell_rows) * 1e6 / rechecks)
                      if rechecks else ""})
    lines = ["Mean rechecks per check: " + ", ".join(f"{t['robots']} robots {t['rechecks_mean']:g}" for t in table)]
    steps = [(a, b) for a, b in zip(table, table[1:]) if a["rechecks_mean"] and b["robots"] == 2 * a["robots"]]
    if steps:
        lines.append("Each doubling of the fleet multiplies them by: " + ", ".join(
            f"{a['robots']} to {b['robots']} robots x{b['rechecks_mean'] / a['rechecks_mean']:.2f}" for a, b in steps))
    fit = [(math.log(t["robots"]), math.log(t["rechecks_mean"])) for t in table if t["rechecks_mean"] > 0]
    if len(fit) >= 2:
        mx, my = sum(x for x, _ in fit) / len(fit), sum(y for _, y in fit) / len(fit)
        slope = sum((x - mx) * (y - my) for x, y in fit) / sum((x - mx) ** 2 for x, _ in fit)
        lines.append(f"Least-squares slope of log(mean rechecks) on log(robots), over {len(fit)} fleet sizes: "
                     f"{slope:.2f} (rechecks grow about as robots^{slope:.2f})")
    timed = [t for t in table if t["ms_per_recheck"] != ""]
    if len(timed) >= 2:
        lines.append(f"Time per recheck: {timed[0]['ms_per_recheck']:g} ms at {timed[0]['robots']} robots, "
                     f"{timed[-1]['ms_per_recheck']:g} ms at {timed[-1]['robots']} robots")
    return table, lines


# ---- repair --------------------------------------------------------------------------------

def repair_scenario_rows(cell: Cell, runs: Sequence[RepairRun], scenarios: Sequence[Scenario]) -> list[dict[str, Any]]:
    index = {sc.scenario_id: sc for sc in scenarios}
    rows = []
    for r in runs:
        row = {**_cell_fields(cell), "index": index[r.scenario_id].index, "scenario_id": r.scenario_id,
               "crossings": index[r.scenario_id].crossings, "outcome": r.outcome, "rounds": r.rounds,
               "passed_in_round": r.passed_in_round or "", "first_findings": r.first_findings,
               "first_s1": r.first_s1, "first_s2": r.first_s2, "first_s4": r.first_s4,
               "targets_changed": r.targets_changed, "tasks_inserted": r.tasks_inserted}
        totals = {k: Counter() for k in _KINDS}
        for c in r.constraints:
            totals.setdefault(c["kind"], Counter()).update({x: c[x] for x in ("received",) + RESULTS})
        for kind in _KINDS:
            for x in ("received",) + RESULTS:
                row[f"{kind}_{x}"] = totals[kind][x]
        rows.append(row)
    return rows


def repair_summary_rows(runs: Mapping[Cell, Sequence[RepairRun]], max_rounds: int = 5) -> list[dict[str, Any]]:
    """Outcomes per cell, then per map and fleet size (densities together), then over all."""
    groups: list[tuple[tuple[str, str, str], list[RepairRun]]] = [
        ((c[0], str(c[1]), c[2]), list(rs)) for c, rs in runs.items()]
    for map_name, robots in dict.fromkeys((c[0], c[1]) for c in runs):
        groups.append(((map_name, str(robots), "*"),
                       [r for c, rs in runs.items() if (c[0], c[1]) == (map_name, robots) for r in rs]))
    groups.append((("*", "*", "*"), [r for rs in runs.values() for r in rs]))
    rows = []
    for (map_name, robots, density), rs in groups:
        t = tally(rs)
        row = {"map": map_name, "robots": robots, "density": density, "scenarios": t["scenarios"],
               "with_conflict": t["with_conflict"]["scenarios"]}
        for prefix, shares, names in (("", t["outcomes"], OUTCOMES), ("wc_", t["with_conflict"]["outcomes"], OUTCOMES[1:])):
            for o in names:
                s = shares.get(o)
                row[f"{prefix}{o}_count"] = s["count"] if s else 0
                row[f"{prefix}{o}_share"] = round(s["share"], 4) if s else ""
                row[f"{prefix}{o}_lo"] = round(s["interval"][0], 4) if s else ""
                row[f"{prefix}{o}_hi"] = round(s["interval"][1], 4) if s else ""
        for k in range(1, max_rounds + 1):
            row[f"passed_round_{k}"] = t["passed_in_round"].get(k, 0)
        rows.append(row)
    return rows


def repair_constraint_rows(runs: Mapping[Cell, Sequence[RepairRun]]) -> list[dict[str, Any]]:
    """What the fake planner did with each kind of constraint, and each kind and rule: per map, then over all."""
    rows = []
    for map_name in list(dict.fromkeys(c[0] for c in runs)) + ["*"]:
        t = tally([r for c, rs in runs.items() if map_name in ("*", c[0]) for r in rs])
        for kind, counts in t["by_kind"].items():
            rows.append({"map": map_name, "kind": kind, "rule_id": "*",
                         **{x: counts.get(x, 0) for x in ("received",) + RESULTS}})
        for (kind, rule), counts in t["by_rule"].items():
            rows.append({"map": map_name, "kind": kind, "rule_id": rule,
                         **{x: counts.get(x, 0) for x in ("received",) + RESULTS}})
    return rows


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with open(path, "w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
