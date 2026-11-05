"""Timing another version of the checker, exporting every verdict, and comparing two versions:
the tools for a fair before/after comparison (compare.py)."""
import csv
import json
import os
import subprocess
import sys

import pytest

from bench import config
from bench.compare import compare_timing, compare_verdicts, parse_cell
from bench.runner import QUICK
from bench.scenarios import cells, people_cells
from bench.verdicts import diff

REPO = config.BENCH_DIR.parents[2]
RUN = config.BENCH_DIR / "run.py"


def run_py(*args):
    return subprocess.run([sys.executable, str(RUN), *args], capture_output=True, text=True, cwd=REPO)


def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_cells_are_named_map_robots_density_people():
    assert parse_cell("bench_grid/32/high/0") == ("bench_grid", 32, "high", 0)
    for bad in ("bench_grid/32/high", "bench_grid/x/high/0", "nowhere/2/low/0", "bench_grid/2/dense/0"):
        with pytest.raises(ValueError):
            parse_cell(bad)


def test_a_run_can_be_kept_to_some_cells(tmp_path):
    done = run_py("--quick", "--only", "perf", "--cell", "narrow_aisle/2/low/0", "--out", str(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert [(r["map"], r["robots"], r["density"]) for r in rows(tmp_path / "perf_summary.csv")] == [
        ("narrow_aisle", "2", "low")]
    assert not (tmp_path / "overhead.csv").exists()
    assert "not run" in (tmp_path / "report.md").read_text().lower()


def test_a_run_can_time_another_source_tree(tmp_path):
    done = run_py("--quick", "--only", "perf", "--cell", "narrow_aisle/2/low/0", "--src", str(config.SRC),
                  "--out", str(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    src = json.loads((tmp_path / "environment.json").read_text())["checker_src"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=REPO).stdout.strip()
    assert (src["path"], src["git_commit"]) == (str(config.SRC), head)
    assert f"`{head}`" in (tmp_path / "report.md").read_text()


def test_every_verdict_is_written_once_and_the_same_every_time(tmp_path):
    for name in ("a", "b"):
        done = run_py("--quick", "--verdicts", "--out", str(tmp_path / name))
        assert done.returncode == 0, done.stdout + done.stderr
    a, b = (tmp_path / "a/verdicts.jsonl").read_bytes(), (tmp_path / "b/verdicts.jsonl").read_bytes()
    assert a == b
    lines = [json.loads(line) for line in a.decode().splitlines()]
    timing = [x for x in lines if x["round"] is None]
    n_cells = len(cells(QUICK.robots)) + len(people_cells(QUICK.people_robots))
    assert len(timing) == n_cells * QUICK.perf_scenarios
    repair = [x for x in lines if x["round"] is not None]
    assert len({x["id"] for x in repair}) == len(cells(QUICK.robots)) * QUICK.repair_scenarios
    assert {"id", "round", "verdict", "rule_ids", "standard_refs", "risk_score", "rationale",
            "constraints"} == set(lines[0])


def test_diff_finds_the_first_line_that_differs(tmp_path):
    (tmp_path / "a").write_text('{"id": "x"}\n{"id": "y"}\n{"id": "z"}\n')
    (tmp_path / "b").write_text('{"id": "x"}\n{"id": "y2"}\n{"id": "z"}\n')
    d = diff(tmp_path / "a", tmp_path / "b")
    assert (d["lines"], d["identical"], d["first_difference"]["line"]) == (3, 2, 2)
    assert diff(tmp_path / "a", tmp_path / "a")["first_difference"] is None


def test_a_timing_comparison_takes_turns_and_logs_the_load(tmp_path):
    two = [("narrow_aisle", 2, "low", 0), ("narrow_aisle", 2, "high", 0)]
    out = compare_timing(str(config.SRC), str(config.SRC), two, tmp_path, quick=True)
    load = rows(out / "load.csv")
    assert [(r["cell"], r["side"]) for r in load] == [
        ("narrow_aisle/2/low/0", "old"), ("narrow_aisle/2/low/0", "new"),
        ("narrow_aisle/2/high/0", "new"), ("narrow_aisle/2/high/0", "old")]
    assert all(r["load_before_1min"] and r["load_after_1min"] for r in load)
    compared = rows(out / "compare.csv")
    assert [(r["map"], r["density"]) for r in compared] == [("narrow_aisle", "low"), ("narrow_aisle", "high")]
    assert all(r["counts_identical"] == "True" for r in compared)
    assert (out / "old_perf_summary.csv").exists() and (out / "new_perf_summary.csv").exists()
    assert "every scenario's counts are the same" in (out / "compare.md").read_text()


def test_a_verdict_comparison_of_one_tree_with_itself_finds_no_difference(tmp_path):
    out = compare_verdicts(str(config.SRC), str(config.SRC), tmp_path, quick=True)
    result = json.loads((out / "equivalence.json").read_text())
    assert result["lines"] == result["identical"] > 0 and result["first_difference"] is None
    assert "identical" in (out / "equivalence.md").read_text()
