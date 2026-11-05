"""A benchmark run: the timing and repair benchmarks over a profile's cells, then the CSV files
and report.md (pure Python). run.py is its command line."""
from __future__ import annotations

import json
import platform
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import config
from .env import environment
from .maps import load_maps
from .perf import Instruments, overhead, run_cell
from .repair import run_scenario
from .report import (overhead_row, people_rows, perf_cell_row, perf_scenario_rows, repair_case_rows,
                     repair_constraint_rows, repair_scenario_rows, repair_summary_rows, write_csv, write_report)
from .scenarios import Cell, cells, check_manifest, people_cells, scenarios_of
from .verdicts import export


@dataclass(frozen=True)
class Profile:
    name: str
    robots: Mapping[str, tuple[int, ...]]
    people_robots: tuple[int, ...]
    perf_scenarios: int
    perf_repetitions: int
    perf_warmup: tuple[int, int]
    repair_scenarios: int
    overhead_scenarios: int
    overhead_repetitions: int


FULL = Profile("full", config.ROBOTS, tuple(config.PEOPLE_SERIES["robots"]), config.PERF_SCENARIOS,
               config.PERF_REPETITIONS, config.PERF_WARMUP, config.REPAIR_SCENARIOS, config.OVERHEAD["scenarios"],
               config.OVERHEAD["repetitions"])
QUICK = Profile("quick", **config.QUICK)


class ManifestError(Exception):
    """The scenarios are not the ones in the manifest; nothing is measured."""


def parameters(profile: Profile) -> dict[str, Any]:
    """Every parameter of a run, for the report."""
    root = config.BENCH_DIR.parents[2]
    return {
        "profile": profile.name, "master_seed": config.MASTER_SEED, "densities (p_cross)": config.DENSITIES,
        "robots per map": {m: list(ns) for m, ns in profile.robots.items()},
        "timing: scenarios per cell": profile.perf_scenarios, "timing: repetitions": profile.perf_repetitions,
        "timing: warm-up (scenarios, repetitions)": list(profile.perf_warmup),
        "S-2 series": {**config.PEOPLE_SERIES, "robots": list(profile.people_robots)},
        "person offset [m]": config.PERSON_OFFSET,
        "overhead check": {"cell": list(config.OVERHEAD["cell"]), "scenarios": profile.overhead_scenarios,
                           "repetitions": profile.overhead_repetitions},
        "repair: scenarios per cell": profile.repair_scenarios, "repair: max attempts": config.REPAIR_MAX_ATTEMPTS,
        "gate settings": config.GATE,
        "maps": {m: str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for m, p in config.MAP_FILES.items()},
        "timer": "time.perf_counter_ns", "percentiles": "nearest rank", "garbage collection": "on",
    }


def default_out(profile: Profile, env: Mapping[str, Any], suffix: str = "") -> Path:
    if profile.name == "quick":
        return config.BENCH_DIR / f"reports/quick{suffix}"
    model = (env["machine"]["model"] or platform.node()).replace(",", "-")
    return config.BENCH_DIR / "reports" / f"{env['date'][:10]}_{model}{suffix}"


def selected(all_cells: Sequence[Cell], cells_filter: set[Cell] | None) -> list[Cell]:
    return [c for c in all_cells if cells_filter is None or c in cells_filter]


def _start(profile: Profile, manifest: Path, argv: Sequence[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    env = environment(argv, profile.name)   # first, so the load is the machine's before the run
    maps = load_maps()
    problems = check_manifest(maps, manifest)
    if problems:
        raise ManifestError(problems)
    return env, maps


def run(profile: Profile, out: Path | None = None, only: str | None = None, manifest: Path = config.MANIFEST,
        argv: Sequence[str] = (), cells_filter: set[Cell] | None = None) -> Path:
    """Time and/or repair a profile's cells (all, or those in cells_filter), and write the results."""
    env, maps = _start(profile, manifest, argv)
    out = out or default_out(profile, env)
    out.mkdir(parents=True, exist_ok=True)
    log = _Log(out / "run.log")
    log(f"{profile.name} run of checker {env['checker_src']['git_commit']}, writing to {out}")
    results: dict[str, Any] = {}
    if only in (None, "perf"):
        results["perf"] = _perf(maps, profile, out, log, cells_filter)
    if only in (None, "repair"):
        results["repair"] = _repair(maps, profile, out, log, cells_filter)
    (out / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    write_report(out / "report.md", env, parameters(profile), results)
    log(f"report: {out / 'report.md'}")
    return out / "report.md"


def export_verdicts(profile: Profile, out: Path | None = None, manifest: Path = config.MANIFEST,
                    argv: Sequence[str] = (), cells_filter: set[Cell] | None = None) -> Path:
    """Write every verdict of a profile's scenarios to verdicts.jsonl (verdicts.py)."""
    env, maps = _start(profile, manifest, argv)
    out = out or default_out(profile, env, "_verdicts")
    out.mkdir(parents=True, exist_ok=True)
    log = _Log(out / "run.log")
    log(f"{profile.name} verdicts of checker {env['checker_src']['git_commit']}, writing to {out}")
    counts = export(maps, profile, out / "verdicts.jsonl", cells_filter, log)
    (out / "environment.json").write_text(json.dumps(env, indent=2) + "\n")
    (out / "verdicts_summary.json").write_text(json.dumps(counts, indent=2) + "\n")
    log(f"{counts['lines']} verdicts, sha256 {counts['sha256']}")
    return out / "verdicts.jsonl"


class _Log:
    def __init__(self, path: Path) -> None:
        self.path, self.start = path, time.monotonic()
        path.write_text("")

    def __call__(self, line: str) -> None:
        line = f"[{time.monotonic() - self.start:8.1f} s] {line}"
        print(line, flush=True)
        with open(self.path, "a") as f:
            f.write(line + "\n")


def _perf(maps: Mapping[str, Any], profile: Profile, out: Path, log: Callable[[str], None],
          cells_filter: set[Cell] | None) -> dict[str, Any]:
    all_cells = selected(cells(profile.robots) + people_cells(profile.people_robots), cells_filter)
    samples, scenarios = {}, {}
    for k, cell in enumerate(all_cells, 1):
        scenarios[cell] = scenarios_of(maps, cell, profile.perf_scenarios)
        start = time.monotonic()
        with Instruments() as inst:
            samples[cell] = run_cell(maps, scenarios[cell], profile.perf_repetitions, profile.perf_warmup, inst)
        log(f"[{k}/{len(all_cells)}] timing {'/'.join(map(str, cell))}: {len(samples[cell])} samples "
            f"in {time.monotonic() - start:.1f} s")
    over = None
    if cells_filter is None:   # the instruments' cost is measured on a full run only
        cell: Cell = tuple(config.OVERHEAD["cell"])
        pairs = overhead(maps, scenarios_of(maps, cell, profile.overhead_scenarios), profile.overhead_repetitions)
        log(f"overhead: {len(pairs)} pairs on {'/'.join(map(str, cell))}")
        over = overhead_row(cell, pairs)
    rows = [perf_cell_row(c, samples[c]) for c in all_cells]
    people = people_rows(samples, scenarios, config.PEOPLE_SERIES["people"])
    write_csv(out / "perf_summary.csv", rows)
    write_csv(out / "perf_scenarios.csv", [r for c in all_cells for r in perf_scenario_rows(c, samples[c], scenarios[c])])
    write_csv(out / "perf_samples.csv", [{"map": c[0], "robots": c[1], "density": c[2], "people": c[3], **asdict(s)}
                                         for c in all_cells for s in samples[c]])
    write_csv(out / "people_series.csv", people)
    if over is not None:
        write_csv(out / "overhead.csv", [over])
    return {"rows": rows, "people": people, "overhead": over}


def _repair(maps: Mapping[str, Any], profile: Profile, out: Path, log: Callable[[str], None],
            cells_filter: set[Cell] | None) -> dict[str, Any]:
    runs, scenarios = {}, {}
    all_cells = selected(cells(profile.robots), cells_filter)
    for k, cell in enumerate(all_cells, 1):
        scenarios[cell] = scenarios_of(maps, cell, profile.repair_scenarios)
        start = time.monotonic()
        runs[cell] = [run_scenario(s, maps[cell[0]], config.REPAIR_MAX_ATTEMPTS) for s in scenarios[cell]]
        log(f"[{k}/{len(all_cells)}] repair {'/'.join(map(str, cell))}: {len(runs[cell])} scenarios "
            f"in {time.monotonic() - start:.1f} s")
    summary = repair_summary_rows(runs, config.REPAIR_MAX_ATTEMPTS)
    constraints = repair_constraint_rows(runs)
    cases = repair_case_rows(runs)
    write_csv(out / "repair_scenarios.csv", [r for c in all_cells for r in repair_scenario_rows(c, runs[c], scenarios[c])])
    write_csv(out / "repair_summary.csv", summary)
    write_csv(out / "repair_constraints.csv", constraints)
    write_csv(out / "repair_cases.csv", cases)
    return {"summary": summary, "constraints": constraints, "cases": cases}
