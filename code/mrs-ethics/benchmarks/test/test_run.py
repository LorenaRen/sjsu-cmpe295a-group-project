"""run.py, the one command: a quick run end to end, the manifest check, and results that do not
depend on the hash seed."""
import csv
import json
import os
import subprocess
import sys

import pytest

from bench import config
from bench.env import environment
from bench.runner import QUICK
from bench.scenarios import cells, people_cells

RUN = config.BENCH_DIR / "run.py"


def run(*args, env=None):
    return subprocess.run([sys.executable, str(RUN), *args], capture_output=True, text=True,
                          env={**os.environ, **(env or {})}, cwd=config.BENCH_DIR.parents[2])


def rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def quick(tmp_path_factory):
    out = tmp_path_factory.mktemp("quick")
    done = run("--quick", "--out", str(out))
    assert done.returncode == 0, done.stdout + done.stderr
    return out


def test_a_quick_run_writes_every_file(quick):
    names = {p.name for p in quick.iterdir()}
    assert names == {"perf_summary.csv", "perf_scenarios.csv", "perf_samples.csv", "people_series.csv",
                     "overhead.csv", "repair_scenarios.csv", "repair_summary.csv", "repair_constraints.csv",
                     "environment.json", "report.md", "run.log"}
    n_cells = len(cells(QUICK.robots)) + len(people_cells(QUICK.people_robots))
    assert len(rows(quick / "perf_summary.csv")) == n_cells
    assert len(rows(quick / "perf_scenarios.csv")) == n_cells * QUICK.perf_scenarios
    assert len(rows(quick / "perf_samples.csv")) == n_cells * QUICK.perf_scenarios * QUICK.perf_repetitions
    assert len(rows(quick / "repair_scenarios.csv")) == len(cells(QUICK.robots)) * QUICK.repair_scenarios


def test_the_report_says_what_was_run_and_where_the_numbers_are(quick):
    text = (quick / "report.md").read_text()
    for heading in ("Quick run", "## Setup", "## Method", "## 1. Checker timing", "### Where the time goes",
                    "### Rechecks against fleet size", "### S-2: the extra time of people",
                    "### What the instruments cost", "## 2. Repairability", "## Limitations"):
        assert heading in text, heading
    env = json.loads((quick / "environment.json").read_text())
    assert env["python"]["hash_seed"] == "0" and env["profile"] == "quick"


def test_the_run_stops_when_the_scenarios_are_not_the_committed_ones(tmp_path):
    lines = config.MANIFEST.read_text().splitlines()
    lines[1] = lines[1][:-8] + "00000000"                   # one digest changed
    tampered = tmp_path / "manifest.csv"
    tampered.write_text("\n".join(lines) + "\n")
    done = run("--quick", "--out", str(tmp_path / "out"), "--manifest", str(tampered))
    assert done.returncode == 2 and "differ" in done.stdout + done.stderr


def test_only_repair_writes_only_the_repair_files(tmp_path):
    done = run("--quick", "--only", "repair", "--out", str(tmp_path))
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (tmp_path / "perf_summary.csv").exists() and (tmp_path / "repair_summary.csv").exists()


def test_what_a_run_finds_does_not_depend_on_the_hash_seed():
    # Everything but the times: findings, rechecks and constraints per check, and how each repair ends.
    code = """
import json
from bench.maps import load_maps
from bench.perf import Instruments, measure
from bench.repair import run_scenario
from bench.scenarios import generate
maps = load_maps()
out = []
for n, d in ((4, "high"), (8, "medium")):
    for i in range(4):
        s = generate(maps, "bench_grid", n, d, i)
        with Instruments() as inst:
            m = measure(s, maps["bench_grid"], inst, rep=0)
        r = run_scenario(s, maps["bench_grid"], max_attempts=5)
        out.append([m.findings, m.rechecks, m.constraints, m.verdict, r.outcome, r.rounds, list(r.constraints)])
print(json.dumps(out))
"""
    outs = [subprocess.run([sys.executable, "-c", code], cwd=config.BENCH_DIR, capture_output=True, text=True,
                           env={**os.environ, "PYTHONHASHSEED": seed}, check=True).stdout for seed in ("1", "424242")]
    assert outs[0] == outs[1]


def test_the_environment_names_the_machine_and_the_python():
    env = environment(["run.py"], "full")
    assert {"date", "command", "git_commit", "git_dirty", "machine", "power", "load_average",
            "busiest_processes", "python", "profile"} <= set(env)
    assert env["python"]["version"].count(".") == 2
