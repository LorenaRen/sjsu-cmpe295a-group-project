# Checker benchmarks

Two reproducible measurements of the shared checker, in a plain Python venv (no ROS):

1. **Timing.** How long `check_team_plan` + `to_verdict` take on generated TeamPlans, split
   into compile, detection, the rechecks that verify each `order_before`, and the rest of the
   verdict. The fleet sizes are 2 to 32 robots, at three conflict densities, on `narrow_aisle`,
   `samples_world` and `bench_grid`, a generated 4 × 4-room map. A small series adds people to
   measure what S-2 costs.
2. **Repairability.** How far the gate's constraints get the fake planner, a scripted planner
   with no LLM (`mrs_ethical_gate/dev_nodes`), run in process against a `GateSession`. The
   counts are how many scenarios are approved in round 1, repaired with every target kept,
   repaired with a target moved, stuck, or out of attempts, and what the fake planner did with
   each kind of constraint. This is not an LLM planner's success rate.

## Run

From the repository root, with the venv the checker's tests use (Python 3.10+, pydantic, PyYAML):

```bash
.venv/bin/python code/mrs-ethics/benchmarks/run.py            # the full run
.venv/bin/python code/mrs-ethics/benchmarks/run.py --quick    # a few scenarios per cell: does it all run?
.venv/bin/python code/mrs-ethics/benchmarks/run.py --only perf      # or --only repair
.venv/bin/python code/mrs-ethics/benchmarks/run.py --out /some/dir
```

- **Before a full run**, plug the computer in and close other programs that use the CPU. The
  report records the power source and the busiest other processes at the start, so a run that
  was not quiet says so.
- **Where the results go.** A full run writes into `reports/<date>_<machine>/`; `--quick` writes
  into `reports/quick/`, which git ignores. (Not `results/`: the workspace's `.gitignore` leaves
  out every `results/` directory, for experiment output.) The run log, `run.log`, gives each cell's wall time.

## What a run writes

| File | Content |
|---|---|
| `report.md` | setup (machine, Python, git commit, power, load), every parameter, the method, and the results; every number in it is computed from the CSV files |
| `perf_summary.csv` | per cell: counts, median / p95 / p99 of the total and of each part, the parts' shares |
| `perf_scenarios.csv` | per scenario: counts and the median of its repetitions of each part |
| `perf_samples.csv` | every timed sample (not kept in git) |
| `people_series.csv` | the S-2 series: the extra time per scenario with people |
| `overhead.csv` | what the timing instruments cost: the same scenarios plain and instrumented |
| `repair_scenarios.csv` | per scenario: outcome, rounds, round-1 findings, targets moved, tasks inserted, and each constraint kind's counts |
| `repair_summary.csv` | outcomes with Wilson 95 % intervals per cell, per map and fleet size, and over all |
| `repair_constraints.csv` | what the fake planner did with each kind of constraint, by kind and rule |
| `environment.json` | the setup, as JSON |

## Reproducing a run

- **Seeds.** Every scenario comes from its own seed string,
  `<MASTER_SEED>/<map>/<robots>/<density>/<index>` (`bench/scenarios.py`). Python's
  `random.Random` gives the same draws for the same str seed on every run and platform.
- **Manifest.** `manifest.csv` holds one digest per cell over all its scenarios. A run checks it
  first and stops with exit code 2 if the generator, the seeds or the maps no longer make the
  same scenarios.
  - To change the scenarios on purpose, run with `--write-manifest`. Results from before and after
  that change are then not comparable.
- **Hash seed.** `run.py` restarts itself with `PYTHONHASHSEED=0`. A test also checks that
  nothing but the times changes with another hash seed.
- **Large map.** `maps/bench_grid.yaml` is written by `bench/maps.py`, from no random input; a
  test checks the file against the generator. `cd code/mrs-ethics/benchmarks && python -m
  bench.maps` writes it again.
- **Parameters.** All of them are in `bench/config.py` and listed in each report.
  - The gate's settings are those of `mrs_ethical_gate/config/gate.yaml`; a test checks that
    they match.

## Comparing two versions of the checker

Run the same command on both commits, on the same machine and under the same conditions, and
compare `perf_summary.csv` cell by cell. The manifest guarantees that both runs timed the same
scenarios, and the report's setup lines say what each one ran on.

## Tests

```bash
cd code/mrs-ethics/benchmarks && ../../../.venv/bin/python -m pytest -q
```
