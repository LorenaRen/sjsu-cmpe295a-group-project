"""Two versions of the checker side by side, on the same benchmark (pure Python; it runs run.py
in subprocesses and may make git worktrees).

compare_timing: for each cell, run.py --only perf --cell <cell> on the old and on the new source
  tree, one process each, so that each starts with its own warm-up. The two take turns going
  first, the old in even cells and the new in odd ones, so that drift over the session (heat,
  background work) falls on both. The load average before and after each process goes into
  load.csv. Each side's perf_summary.csv is merged; compare.csv gives, per cell, the old median,
  p95 and p99 over the new (above 1: the new is faster). Both sides compute the same verdicts, so
  every scenario's findings, rechecks, constraints and verdict must match; compare.md says
  whether they did, and the comparison counts only if they did.

compare_verdicts: run.py --verdicts on both trees, then the two verdicts.jsonl line by line.

A source tree is a directory (a code/mrs-ethics/src) or a git commit, which is checked out into a
temporary git worktree for the run and removed afterwards. The benchmark's own code is always
this checkout's: only the checker differs.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from . import config
from .env import environment
from .report import _ms, _table, kept_awake_row
from .runner import FULL, QUICK, _Log
from .scenarios import Cell, cells, people_cells
from .verdicts import diff

REPO = config.BENCH_DIR.parents[2]
RUN = config.BENCH_DIR / "run.py"
_COUNTS = ("verdict", "findings", "s1", "s2", "s4", "constraints", "rechecks")


def parse_cell(text: str) -> Cell:
    parts = text.split("/")
    if len(parts) != 4:
        raise ValueError(f"{text!r}: a cell is map/robots/density/people, e.g. bench_grid/32/high/0")
    map_name, robots, density, people = parts
    if map_name not in config.MAP_FILES or density not in config.DENSITIES or not robots.isdigit() \
            or not people.isdigit():
        raise ValueError(f"{text!r}: no such cell (maps {sorted(config.MAP_FILES)}, densities "
                         f"{sorted(config.DENSITIES)})")
    return (map_name, int(robots), density, int(people))


def cell_id(cell: Cell) -> str:
    return "/".join(map(str, cell))


def _git(*args: str, cwd: Path = REPO) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@contextmanager
def source_tree(spec: str) -> Iterator[Path]:
    """A checker source tree: spec is a code/mrs-ethics/src directory, or a git commit checked out
    into a temporary worktree for as long as it is needed."""
    if Path(spec).is_dir():
        yield Path(spec).resolve()
        return
    tmp = Path(tempfile.mkdtemp(prefix="bench-src-"))
    _git("worktree", "add", "--detach", str(tmp / "tree"), spec)
    try:
        yield (tmp / "tree/code/mrs-ethics/src").resolve()
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tmp / "tree")], cwd=REPO, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def _source(spec: str, path: Path) -> dict[str, Any]:
    return {"spec": spec, "path": str(path), "git_commit": _git("rev-parse", "HEAD", cwd=path)}


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def compare_timing(old: str, new: str, cell_list: Sequence[Cell] | None, out: Path, quick: bool = False) -> Path:
    profile = QUICK if quick else FULL
    cell_list = list(cell_list or cells(profile.robots) + people_cells(profile.people_robots))
    out.mkdir(parents=True, exist_ok=True)
    env = environment(sys.argv, f"compare ({profile.name})")
    log = _Log(out / "compare.log")
    load: list[dict[str, Any]] = []
    with source_tree(old) as old_src, source_tree(new) as new_src:
        srcs = {"old": old_src, "new": new_src}
        env["sources"] = {"old": _source(old, old_src), "new": _source(new, new_src)}
        for k, cell in enumerate(cell_list):
            for position, side in enumerate(("old", "new") if k % 2 == 0 else ("new", "old"), 1):
                before, start = os.getloadavg(), time.monotonic()
                done = subprocess.run([sys.executable, str(RUN), "--only", "perf", "--cell", cell_id(cell),
                                       "--src", str(srcs[side]), "--out", str(out / "runs" / side / cell_id(cell))]
                                      + (["--quick"] if quick else []), capture_output=True, text=True)
                if done.returncode:
                    raise RuntimeError(f"{side} {cell_id(cell)}: run.py failed\n{done.stdout[-3000:]}{done.stderr[-3000:]}")
                after = os.getloadavg()
                load.append({"cell": cell_id(cell), "side": side, "position": position,
                             "seconds": round(time.monotonic() - start, 1),
                             **{f"load_before_{m}": round(x, 2) for m, x in zip(("1min", "5min", "15min"), before)},
                             **{f"load_after_{m}": round(x, 2) for m, x in zip(("1min", "5min", "15min"), after)}})
                log(f"[{k + 1}/{len(cell_list)}] {side} {cell_id(cell)}: {load[-1]['seconds']} s, "
                    f"load {before[0]:.2f} -> {after[0]:.2f}")
    summary = {side: [_rows(out / "runs" / side / cell_id(c) / "perf_summary.csv")[0] for c in cell_list]
               for side in ("old", "new")}
    for side, rows in summary.items():
        _write(out / f"{side}_perf_summary.csv", rows)
    scenarios = {side: {r["scenario_id"]: r for c in cell_list
                        for r in _rows(out / "runs" / side / cell_id(c) / "perf_scenarios.csv")}
                 for side in ("old", "new")}
    compared, differing = [], []
    for c, o, n in zip(cell_list, summary["old"], summary["new"]):
        ids = [sid for sid in scenarios["old"] if sid.startswith(cell_id(c) + "/")]
        bad = [sid for sid in ids if any(scenarios["old"][sid][k] != scenarios["new"].get(sid, {}).get(k)
                                         for k in _COUNTS)]
        differing += bad
        ratio = lambda key: round(float(o[key]) / float(n[key]), 3) if float(n[key]) else ""   # noqa: E731
        compared.append({"map": c[0], "robots": c[1], "density": c[2], "people": c[3], "samples": o["samples"],
                         "findings_mean": o["findings_mean"], "rechecks_mean_old": o["rechecks_mean"],
                         "rechecks_mean_new": n["rechecks_mean"], "counts_identical": not bad,
                         "old_median_ms": o["total_median_ms"], "new_median_ms": n["total_median_ms"],
                         "speedup_median": ratio("total_median_ms"),
                         "old_p95_ms": o["total_p95_ms"], "new_p95_ms": n["total_p95_ms"], "speedup_p95": ratio("total_p95_ms"),
                         "old_p99_ms": o["total_p99_ms"], "new_p99_ms": n["total_p99_ms"], "speedup_p99": ratio("total_p99_ms"),
                         "old_recheck_median_ms": o["recheck_median_ms"], "new_recheck_median_ms": n["recheck_median_ms"]})
    _write(out / "compare.csv", compared)
    _write(out / "load.csv", load)
    (out / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    n_scenarios = sum(len([s for s in scenarios["old"] if s.startswith(cell_id(c) + "/")]) for c in cell_list)
    _compare_md(out / "compare.md", env, profile.name, compared, load, differing, n_scenarios)
    log(f"compare: {out / 'compare.md'}")
    return out


def _compare_md(path: Path, env: dict[str, Any], profile: str, compared: list[dict[str, Any]],
                load: list[dict[str, Any]], differing: list[str], n_scenarios: int) -> None:
    m, py, src = env["machine"], env["python"], env["sources"]
    lines = [f"# Checker comparison, {env['date'][:10]}", ""] + _table(["", ""], [
        ("Command", f"`{env['command']}`"), ("Kept awake by", kept_awake_row(env)),
        ("Old checker", f"`{src['old']['git_commit']}` ({src['old']['spec']})"),
        ("New checker", f"`{src['new']['git_commit']}` ({src['new']['spec']})"),
        ("Machine", f"{m['model']}, {m['cpu']}, {m['performance_cores']} performance + {m['efficiency_cores']} "
                    f"efficiency cores, {m['memory_gb']} GB memory, {m['os']}"),
        ("Power", env["power"] or "-"),
        ("Load average at the start (1, 5, 15 min)", ", ".join(f"{x:g}" for x in env["load_average"])),
        ("Busiest other processes at the start (% CPU)",
         "; ".join(f"{p['command']} {p['cpu_percent']:g}" for p in env["busiest_processes"]) or "-"),
        ("Python", f"{py['implementation']} {py['version']}, pydantic {py['pydantic']}, PYTHONHASHSEED={py['hash_seed']}"),
        ("Profile", profile)])
    lines += ["", "Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old "
                  "side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for "
                  "both sides: only the checker differs. Speedup is the old value over the new.", ""]
    if differing:
        lines += [f"**The comparison does not count: {len(differing)} of {n_scenarios} scenarios' counts differ "
                  f"between the sides** (first: `{differing[0]}`).", ""]
    else:
        lines += [f"Both sides computed the same work: every scenario's counts are the same ({n_scenarios} scenarios: "
                  f"verdict, findings by rule, constraints, rechecks).", ""]
    lines += _table(["map", "robots", "density", "people", "median old", "new", "speedup", "p95 old", "new", "speedup",
                     "p99 old", "new", "speedup", "recheck median old", "new"],
                    [(r["map"], r["robots"], r["density"], r["people"], _ms(r["old_median_ms"]), _ms(r["new_median_ms"]),
                      r["speedup_median"], _ms(r["old_p95_ms"]), _ms(r["new_p95_ms"]), r["speedup_p95"],
                      _ms(r["old_p99_ms"]), _ms(r["new_p99_ms"]), r["speedup_p99"], _ms(r["old_recheck_median_ms"]),
                      _ms(r["new_recheck_median_ms"])) for r in compared])
    lines += ["", "Times in ms. Load average before and after each process (1 min):", ""]
    lines += _table(["cell", "side", "order", "seconds", "load before", "load after"],
                    [(x["cell"], x["side"], x["position"], x["seconds"], x["load_before_1min"], x["load_after_1min"])
                     for x in load])
    path.write_text("\n".join(lines) + "\n")


def compare_verdicts(old: str, new: str, out: Path, quick: bool = False) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    env = environment(sys.argv, f"verdicts ({'quick' if quick else 'full'})")
    with source_tree(old) as old_src, source_tree(new) as new_src:
        env["sources"] = {"old": _source(old, old_src), "new": _source(new, new_src)}
        for side, src in (("old", old_src), ("new", new_src)):
            done = subprocess.run([sys.executable, str(RUN), "--verdicts", "--src", str(src), "--out", str(out / side)]
                                  + (["--quick"] if quick else []), capture_output=True, text=True)
            if done.returncode:
                raise RuntimeError(f"{side}: run.py --verdicts failed\n{done.stdout[-3000:]}{done.stderr[-3000:]}")
    result = diff(out / "old/verdicts.jsonl", out / "new/verdicts.jsonl")
    counts = {side: json.loads((out / side / "verdicts_summary.json").read_text()) for side in ("old", "new")}
    result.update(sources=env["sources"], counts=counts)
    (out / "equivalence.json").write_text(json.dumps(result, indent=2) + "\n")
    (out / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    c = counts["old"]
    same = result["first_difference"] is None and result["old_lines"] == result["new_lines"]
    text = [f"# Verdict equivalence, {env['date'][:10]}", "",
            f"Old checker `{env['sources']['old']['git_commit']}`, new checker `{env['sources']['new']['git_commit']}`. "
            f"Every verdict of the benchmark's scenarios: {c['timing_verdicts']} on the timing scenarios as generated, "
            f"{c['repair_verdicts']} in the rounds of the repair runs.", "",
            (f"**All {result['lines']} verdicts are identical**, byte for byte (SHA-256 of both files "
             f"`{result['sha256']['old']}`)." if same else
             f"**{result['lines'] - result['identical']} of {result['lines']} lines differ**; the first is line "
             f"{result['first_difference']['line']} (equivalence.json has both versions of it)."), ""]
    (out / "equivalence.md").write_text("\n".join(text))
    return out
