"""The report's numbers: computed from samples and runs, checked here on made-up ones."""
import math

import pytest

from bench.perf import OverheadPair, Sample
from bench.report import (bottleneck_lines, growth_lines, overhead_row, people_rows, perf_cell_row,
                          perf_scenario_rows, repair_constraint_rows, repair_summary_rows)
from bench.repair import RepairRun
from bench.scenarios import Scenario

MS = 1_000_000   # ns


def sample(sid, rep, compile_ms, detect_ms, other_ms, recheck_ms, rechecks, findings=1):
    verdict_ns = (other_ms + recheck_ms) * MS
    return Sample(sid, rep, total_ns=(compile_ms + detect_ms) * MS + verdict_ns, check_ns=(compile_ms + detect_ms) * MS,
                  compile_ns=compile_ms * MS, detect_ns=detect_ms * MS, verdict_ns=verdict_ns,
                  recheck_ns=recheck_ms * MS, recheck_compile_ns=recheck_ms * MS // 2, rechecks=rechecks,
                  verdict="REJECT" if findings else "APPROVE", findings=findings, constraints=findings,
                  s1=findings, s2=0, s4=0)


def scenario(map_name, n, density, index, people=()):
    return Scenario(map_name, n, density, index, f"seed/{index}", {}, None, crossings=index, people=people)


def test_a_cells_row_has_percentiles_and_the_share_of_each_part():
    samples = [sample("m/2/low/0/0", r, 1, 2, 3, 4, rechecks=2) for r in range(3)]
    row = perf_cell_row(("m", 2, "low", 0), samples)
    assert (row["samples"], row["total_median_ms"], row["total_p99_ms"]) == (3, 10.0, 10.0)
    assert (row["share_compile"], row["share_detect"], row["share_rechecks"], row["share_constraints_other"]) == (
        0.1, 0.2, 0.4, 0.3)
    assert (row["rechecks_mean"], row["ms_per_recheck"], row["findings_mean"]) == (2.0, 2.0, 1.0)


def test_a_scenarios_row_takes_the_median_of_its_repetitions():
    s = scenario("m", 2, "low", 0)
    samples = [sample(s.scenario_id, r, c, 0, 0, 0, 0) for r, c in enumerate((5, 1, 3))]
    [row] = perf_scenario_rows(("m", 2, "low", 0), samples, [s])
    assert (row["total_median_ms"], row["compile_median_ms"], row["crossings"]) == (3.0, 3.0, 0)


def test_the_people_series_compares_each_scenario_with_and_without_people():
    plain = [scenario("g", 8, "medium", i) for i in range(4)]
    crowded = [scenario("g", 8, "medium", i, people=("p",) * 4) for i in range(4)]
    base = [sample(s.scenario_id, 0, 1, 2, 0, 0, 0) for s in plain]
    more = [sample(s.scenario_id, 0, 1, 2 + i, 1, 0, 0) for i, s in enumerate(crowded)]   # +i detection, +1 verdict
    [row] = people_rows({("g", 8, "medium", 0): base, ("g", 8, "medium", 4): more},
                        {("g", 8, "medium", 0): plain, ("g", 8, "medium", 4): crowded}, people=4)
    assert (row["robots"], row["scenarios"]) == (8, 4)
    assert (row["extra_median_ms"], row["extra_p95_ms"]) == (2.0, 4.0)          # 1 + i, nearest rank
    assert (row["detect_extra_median_ms"], row["constraints_extra_median_ms"]) == (1.0, 1.0)


def test_the_overhead_row_is_the_median_of_paired_differences():
    pairs = [OverheadPair("x", 0, plain_ns=10 * MS, timed_ns=11 * MS), OverheadPair("y", 0, 20 * MS, 20 * MS),
             OverheadPair("z", 0, 30 * MS, 33 * MS)]
    row = overhead_row(("g", 8, "medium", 0), pairs)
    assert (row["pairs"], row["plain_median_ms"], row["diff_median_ms"]) == (3, 20.0, 1.0)


def test_the_bottleneck_lines_name_the_largest_part_of_each_fleet_size():
    rows = [perf_cell_row(("g", n, d, 0), [sample(f"g/{n}/{d}/0/0", 0, 1, 1, 1, r, 1)])
            for n, r in ((4, 0), (8, 5), (16, 50)) for d in ("low", "high")]
    lines = bottleneck_lines(rows, "g")
    assert any("4 robots" in line and "compile" in line for line in lines)   # a tie at 4 robots: the first part
    assert any("16 robots" in line and "rechecks" in line and "94" in line for line in lines)   # 50 / 53 of the time


def test_the_growth_lines_fit_how_rechecks_grow_with_the_fleet():
    rows = [perf_cell_row(("g", n, "low", 0), [sample(f"g/{n}/low/0/0", 0, 1, 1, 1, 1, n * n)]) for n in (2, 4, 8, 16)]
    table, lines = growth_lines(rows, "g")
    assert [r["robots"] for r in table] == [2, 4, 8, 16]
    assert [r["rechecks_mean"] for r in table] == [4.0, 16.0, 64.0, 256.0]
    assert any("2.00" in line for line in lines)                              # log-log slope of n^2


def runs_for(map_name, n, density, outcomes):
    return [RepairRun(f"{map_name}/{n}/{density}/0/{i}", o, 1 if o == "first_round" else 2,
                      1 if o == "first_round" else (2 if o.endswith("targets") else None),
                      0 if o == "first_round" else 1, 0, 0, 0, int(o == "changed_targets"), 0,
                      ({"kind": "order_before", "rule_id": "capacity_exceeded", "received": 1, "changed": 1,
                        "noted": 0, "not_applied": 0},) if o != "first_round" else ())
            for i, o in enumerate(outcomes)]


def test_repair_rows_per_cell_per_map_and_fleet_and_overall():
    runs = {("m", 2, "low", 0): runs_for("m", 2, "low", ["first_round", "stuck"]),
            ("m", 2, "high", 0): runs_for("m", 2, "high", ["kept_targets", "changed_targets"])}
    rows = repair_summary_rows(runs)
    assert [(r["map"], r["robots"], r["density"]) for r in rows] == [
        ("m", "2", "low"), ("m", "2", "high"), ("m", "2", "*"), ("*", "*", "*")]
    overall = rows[-1]
    assert (overall["scenarios"], overall["with_conflict"], overall["first_round_count"], overall["stuck_count"]) == (
        4, 3, 1, 1)
    assert math.isclose(overall["kept_targets_share"], 0.25)
    kinds = repair_constraint_rows(runs)
    assert {"map": "*", "kind": "order_before", "rule_id": "*", "received": 3, "changed": 3, "noted": 0,
            "not_applied": 0} in kinds
