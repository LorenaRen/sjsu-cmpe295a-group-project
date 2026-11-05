"""The numbers of a benchmark run, as CSV rows and as the sentences of report.md (pure Python).

Every number in report.md is computed here from the samples and runs that the CSV files next
to it hold; the text around the numbers is fixed. Times are in ms, rounded to 0.1 us; shares
are of the summed time of all samples of a cell (or of all cells pooled), so they add up.
"""
from __future__ import annotations

import csv
import json
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
    for cell in [c for c in samples if c[3] == people and c[:3] + (0,) in samples]:
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


def repair_case_rows(runs: Mapping[Cell, Sequence[RepairRun]]) -> list[dict[str, Any]]:
    """What the fake planner did with the constraints of each situation (VerdictContent.cases), by
    kind and rule: per map, then over all."""
    rows = []
    for map_name in list(dict.fromkeys(c[0] for c in runs)) + ["*"]:
        t = tally([r for c, rs in runs.items() if map_name in ("*", c[0]) for r in rs])
        for (kind, rule, case), counts in t["by_case"].items():
            rows.append({"map": map_name, "kind": kind, "rule_id": rule, "case": case,
                         **{x: counts.get(x, 0) for x in ("received",) + RESULTS}})
    return rows


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with open(path, "w", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# ---- report.md -------------------------------------------------------------------------------

def _largest_by_fleet(rows: Sequence[Mapping[str, Any]], map_name: str) -> list[tuple[int, str, dict[str, float]]]:
    out = []
    for n in sorted({r["robots"] for r in rows if r["map"] == map_name and not r["people"]}):
        shares = _pooled_shares([r for r in rows if r["map"] == map_name and r["robots"] == n and not r["people"]])
        out.append((n, max(PARTS, key=lambda p: shares[p]), shares))
    return out


def bottleneck_answer(rows: Sequence[Mapping[str, Any]], map_name: str) -> str | None:
    """One sentence: from which fleet size on one part takes the largest share, and how much."""
    per_n = _largest_by_fleet(rows, map_name)
    if not per_n:
        return None
    n_last, part, shares = per_n[-1]
    n_from = n_last
    for n, p, _ in reversed(per_n):
        if p != part:
            break
        n_from = n
    verb = "take" if part == "rechecks" else "takes"
    text = (f"On {map_name}, from {n_from} robots on, {part} {verb} the largest share of the time: "
            f"{shares[part]:.0%} at {n_last} robots.")
    below = [(n, p) for n, p, _ in per_n if n < n_from]
    if below:
        text += " Below that, the largest share is " + ", ".join(f"{p} at {n} robots" for n, p in below) + "."
    return text


def _ms(x: Any) -> str:
    if x == "" or x is None:
        return "-"
    x = float(x)
    return f"{x:.0f}" if x >= 100 else f"{x:.1f}" if x >= 10 else f"{x:.2f}" if x >= 1 else f"{x:.3f}"


def _num(x: Any) -> str:
    return "-" if x == "" or x is None else f"{float(x):.3g}"


def _pct(x: Any) -> str:
    if x == "" or x is None:
        return "-"
    return f"{float(x):.0%}" if float(x) >= 0.1 else f"{float(x):.1%}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> list[str]:
    return (["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
            + ["| " + " | ".join(str(c) for c in row) + " |" for row in rows])


def _share_cell(row: Mapping[str, Any], prefix: str, outcome: str) -> str:
    count = row[f"{prefix}{outcome}_count"]
    if row[f"{prefix}{outcome}_share"] == "":
        return "-"
    return (f"{_pct(row[f'{prefix}{outcome}_share'])} [{_pct(row[f'{prefix}{outcome}_lo'])}–"
            f"{_pct(row[f'{prefix}{outcome}_hi'])}] ({count})")


_METHOD = """\
**Scenarios** (`bench/scenarios.py`). Every scenario has its own seed string,
`<master_seed>/<map>/<robots>/<density>/<index>`, for Python's `random.Random`; a str seed and
the generator are the same on every platform. Start nodes: distinct nodes of the map's open
areas, where a robot may end its plan without breaking P-1 or P-2, so that the findings are
conflicts between robots. Targets, robot by robot: from the other half of the map with
probability p_cross (the density), else from the robot's own half; never another robot's target
or its own start (a half with no node left gives way to the other). One `hold_position` task per
robot, with no `depends_on`. The S-2 series measures the same scenarios as its cells with people
added, each near a random open-area node. `manifest.csv` holds a digest per cell over all its
scenarios; a run stops if the generator no longer makes them.

**Timing** (`bench/perf.py`). One process, the cells one after another. Before a cell, its first
scenarios run untimed (the warm-up) and `gc.collect()` runs; then every scenario of the cell is
timed once per repetition, the scenarios in order within each repetition. Garbage collection
stays on, as in the gate. A sample is `check_team_plan` then `to_verdict` on its result, with the
gate's settings and no ledger, timed with `time.perf_counter_ns`, and split into:

- **compile**: the check's own `compile_team_plan` (routes and times of every robot);
- **detection**: the rest of `check_team_plan` (P-1, P-2, P-3, S-1, S-2 with people, S-4, R-1,
  who must change);
- **rechecks**: within `to_verdict`, each `order_before` it proposes is verified by adding it to
  a copy of the plan and running the whole check again (compile and detection);
- **other constraint work**: the rest of `to_verdict`.

The split comes from wrapping `compile_team_plan` (as `team_check` calls it) and
`TeamCheckResult.recheck` during the run; the checker's code is not changed. Percentiles are
nearest rank, so each is a measured sample. Shares are of the summed time of the samples.

**Repairability** (`bench/repair.py`). Each scenario runs through a new `GateSession` and the
fake planner (`mrs_ethical_gate/dev_nodes`), wired as the two nodes are but in one process. It
ends in one of: approved in round 1 (no finding); approved later with every target kept (only
`depends_on` added, or waits inserted); approved later with a target moved; stuck (a REJECT the
fake planner could change nothing for); attempts exhausted. Intervals are Wilson 95 %.
"""

_LIMITS = """\
- One laptop process. Apple silicon has performance and efficiency cores, and macOS does not let
  a process be pinned to one kind; the run log has each cell's wall time.
- The times include the wrapping that splits them; its cost is measured above.
- Every plan has one task per robot and no `depends_on`, and every target is in an open area:
  the findings are S-1 and S-4 conflicts (and S-2 in its series), not P-1, P-2 or P-3.
- The small maps take two or four robots: each has six open-area nodes.
- S-2 is measured only in its series, with people standing still where they are drawn.
- Repairability is that of the fake planner, which applies constraints mechanically. It is not
  the success rate of an LLM planner.
"""


def kept_awake_row(env: Mapping[str, Any]) -> str:
    """The setup row that says whether the Mac could sleep during the run (older environments lack the key)."""
    return env.get("kept_awake_by") or "no caffeinate assertion found for this run (`pmset -g assertions`)"


def write_report(path: Path, env: Mapping[str, Any], params: Mapping[str, Any], results: Mapping[str, Any]) -> None:
    out: list[str] = [f"# Checker benchmarks, {env['date'][:10]}", ""]
    if env["profile"] == "quick":
        out += ["> **Quick run** (profile `quick`): a few scenarios per cell, to check that everything runs. "
                "Its numbers are not for comparison.", ""]
    m, py = env["machine"], env["python"]
    out += ["## Setup", ""] + _table(["", ""], [
        ("Command", f"`{env['command']}`"),
        ("Date", env["date"]),
        ("Code", f"git `{env['git_commit']}`" + (", with uncommitted changes" if env["git_dirty"] else "")),
        ("Machine", f"{m['model']}, {m['cpu']}, {m['performance_cores']} performance + {m['efficiency_cores']} "
                    f"efficiency cores, {m['memory_gb']} GB memory, {m['os']}"),
        ("Power", env["power"] or "-"),
        ("Kept awake by", kept_awake_row(env)),
        ("Load average at the start (1, 5, 15 min)", ", ".join(f"{x:g}" for x in env["load_average"])),
        ("Busiest other processes at the start (% CPU)",
         "; ".join(f"{p['command']} {p['cpu_percent']:g}" for p in env["busiest_processes"]) or "-"),
        ("Python", f"{py['implementation']} {py['version']} (`{py['executable']}`), pydantic {py['pydantic']}, "
                   f"PYTHONHASHSEED={py['hash_seed']}"),
        ("Checker source", f"`{env['checker_src']['path']}`, git `{env['checker_src']['git_commit']}`"
                           + (", with uncommitted changes" if env["checker_src"]["git_dirty"] else "")),
    ])
    out += ["", "### Parameters", ""] + _table(["Parameter", "Value"], [
        (k, f"`{json.dumps(v)}`" if not isinstance(v, str) else v) for k, v in params.items()])
    out += ["", "## Method", "", _METHOD]
    if "perf" in results:
        out += _perf_section(results["perf"])
    if "repair" in results:
        out += _repair_section(results["repair"])
    out += ["## Limitations", "", _LIMITS]
    path.write_text("\n".join(out))


def _perf_section(perf: Mapping[str, Any]) -> list[str]:
    rows = perf["rows"]
    main = [r for r in rows if not r["people"]]
    out = ["## 1. Checker timing", "", "### Per cell", "",
           "Times in ms over all samples of a cell; findings and rechecks are means per check. The shares are "
           "of the cell's summed time.", ""]
    out += _table(["map", "robots", "density", "samples", "findings/check", "rechecks/check", "median", "p95", "p99",
                   "compile %", "detection %", "rechecks %", "other %"],
                  [(r["map"], r["robots"], r["density"], r["samples"], _num(r["findings_mean"]),
                    _num(r["rechecks_mean"]), _ms(r["total_median_ms"]), _ms(r["total_p95_ms"]),
                    _ms(r["total_p99_ms"]), _pct(r["share_compile"]), _pct(r["share_detect"]),
                    _pct(r["share_rechecks"]), _pct(r["share_constraints_other"])) for r in main])
    out += ["", "Medians, p95 and p99 of each part, per cell, are in `perf_summary.csv`; each scenario's in "
                "`perf_scenarios.csv`.", "", "### Where the time goes", ""]
    for map_name in dict.fromkeys(r["map"] for r in main):
        answer = bottleneck_answer(main, map_name)
        out += [f"**{map_name}.** {answer}", ""] + [f"- {line}" for line in bottleneck_lines(main, map_name)] + [""]
    if any(r["map"] == "bench_grid" for r in main):
        table, lines = growth_lines(main, "bench_grid")
        out += ["### Rechecks against fleet size", "",
                "On bench_grid, the densities pooled: rechecks are the `order_before` verifications of one "
                "check's verdict.", ""]
        out += _table(["robots", "findings per check", "rechecks per check", "ms per recheck"],
                      [(t["robots"], _num(t["findings_mean"]), _num(t["rechecks_mean"]), _ms(t["ms_per_recheck"]))
                       for t in table])
        out += [""] + [f"- {line}" for line in lines] + [""]
    if perf["people"]:
        out += ["### S-2: the extra time of people", "",
                "The same scenarios with and without the people; the extra time is each scenario's median with "
                "people less its median without, then over the scenarios (ms).", ""]
        out += _table(["map", "robots", "density", "people", "scenarios", "S-2 findings per check",
                       "median without", "median with", "extra: median", "extra: p95", "of it in detection",
                       "in the verdict", "rechecks without / with"],
                      [(p["map"], p["robots"], p["density"], p["people"], p["scenarios"], _num(p["s2_findings_mean"]),
                        _ms(p["total_median_ms_without"]), _ms(p["total_median_ms_with"]), _ms(p["extra_median_ms"]),
                        _ms(p["extra_p95_ms"]), _ms(p["detect_extra_median_ms"]),
                        _ms(p["constraints_extra_median_ms"]),
                        f"{_num(p['rechecks_mean_without'])} / {_num(p['rechecks_mean_with'])}") for p in perf["people"]])
        out.append("")
    o = perf["overhead"]
    out += ["### What the instruments cost", ""]
    if o is None:
        return out + ["Not run: only some cells were run (`--cell`).", ""]
    out += [f"On {o['map']}, {o['robots']} robots, {o['density']}: {o['pairs']} pairs of the same scenario plain and "
            f"instrumented. Instrumented less plain: median {_ms(o['diff_median_ms'])} ms "
            f"({_pct(o['diff_share'])} of the plain median of {_ms(o['plain_median_ms'])} ms), "
            f"p95 {_ms(o['diff_p95_ms'])} ms.", ""]
    return out


def _repair_section(repair: Mapping[str, Any]) -> list[str]:
    summary = [r for r in repair["summary"] if r["density"] == "*"]
    out = ["## 2. Repairability", "", "### Outcomes", "",
           "Per map and fleet size, the densities together; share [Wilson 95 %] (count). Each cell's are in "
           "`repair_summary.csv`, each scenario's in `repair_scenarios.csv`.", ""]
    out += _table(["map", "robots", "scenarios", "first round", "kept targets", "changed targets", "stuck", "exhausted"],
                  [(r["map"], r["robots"], r["scenarios"], *(_share_cell(r, "", o) for o in OUTCOMES)) for r in summary])
    out += ["", "### Plans with a conflict in round 1", ""]
    out += _table(["map", "robots", "with a conflict", "kept targets", "changed targets", "stuck", "exhausted"],
                  [(r["map"], r["robots"], r["with_conflict"], *(_share_cell(r, "wc_", o) for o in OUTCOMES[1:]))
                   for r in summary])
    overall = summary[-1]
    rounds = [k for k in overall if k.startswith("passed_round_")]
    out += ["", "### The round approved plans passed in", ""]
    out += _table([k.replace("passed_round_", "round ") for k in rounds], [[overall[k] for k in rounds]])
    out += ["", "### What the fake planner did with each kind of constraint", "",
            "Over all scenarios: changed the plan, noted it (nothing to change; kept out of the robot's tasks from "
            "then on), or could not apply it.", ""]
    kinds = [c for c in repair["constraints"] if c["map"] == "*"]
    out += _table(["kind", "rule", "received", "changed", "noted", "not applied"],
                  [(c["kind"], c["rule_id"], c["received"], c["changed"], c["noted"], c["not_applied"]) for c in kinds])
    s4 = sorted((c for c in repair.get("cases", []) if c["map"] == "*" and c["rule_id"] == "node_occupied"),
                key=lambda c: -c["received"])
    out += ["", "### S-4's constraints by situation", "",
            "Over all scenarios, the situation each S-4 constraint answers (`VerdictContent.cases`; "
            "every rule's are in `repair_cases.csv`).", ""]
    out += _table(["situation", "kind", "received", "changed", "noted", "not applied"],
                  [(f"`{c['case']}`: {S4_CASES.get(c['case'], '-')}", c["kind"], c["received"], c["changed"],
                    c["noted"], c["not_applied"]) for c in s4]) if s4 else ["None."]
    return out + [""]


# S-4's situations (verdict._stop_constraint), for the report; the benchmarks also run checkers
# from before VerdictContent.cases, so the names are not imported from the checker.
S4_CASES = {
    "yielder_waits": "the yielder waits until the keeper has left (verified)",
    "keeper_waits": "the keeper, there to the end of its plan, waits until the yielder has gone by (verified)",
    "keeper_ends_elsewhere": "the keeper, there to the end of its plan, cannot wait: it ends somewhere else",
    "no_waiting_there": "the yielder only waits there between tasks: it waits somewhere else",
    "from_start": "the yielder is at the node when its plan starts",
    "on_its_edge": "the yielder gets there on the edge it is on now: the keeper keeps clear",
    "cannot_be_done": "the keeper stays there to the end of its plan, where a task of the yielder ends",
    "passes_waiting_fails": "the yielder drives through, and waiting for the keeper does not help",
    "wait_nearby": "a task of the yielder ends there, and waiting for the keeper does not help",
    "cycle": "an ordering that, with the verdict's other orderings, would close a cycle",
}
