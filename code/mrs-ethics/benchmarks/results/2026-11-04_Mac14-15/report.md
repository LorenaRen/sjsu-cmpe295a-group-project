# Checker benchmarks, 2026-11-04

## Setup

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/run.py` |
| Date | 2026-11-04T17:05:05-08:00 |
| Code | git `179f804c487ed033c5655f0497c8b2e532309513` |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 3.1, 2.93, 2.48 |
| Busiest other processes at the start (% CPU) | WindowServer 21.7; fileproviderd 14.1; coreaudiod 10.9; syspolicyd 9.2; Google Chrome Helper (Aperitif) 7.8 |
| Python | CPython 3.14.4 (`/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python`), pydantic 2.14.1, PYTHONHASHSEED=0 |

### Parameters

| Parameter | Value |
|---|---|
| profile | full |
| master_seed | mrs-ethics-benchmarks-1 |
| densities (p_cross) | `{"low": 0.0, "medium": 0.5, "high": 1.0}` |
| robots per map | `{"narrow_aisle": [2, 4], "samples_world": [2, 4], "bench_grid": [2, 4, 8, 16, 32]}` |
| timing: scenarios per cell | `200` |
| timing: repetitions | `5` |
| timing: warm-up (scenarios, repetitions) | `[5, 2]` |
| S-2 series | `{"map": "bench_grid", "robots": [8, 16], "density": "medium", "people": 4}` |
| person offset [m] | `0.5` |
| overhead check | `{"cell": ["bench_grid", 8, "medium", 0], "scenarios": 50, "repetitions": 4}` |
| repair: scenarios per cell | `50` |
| repair: max attempts | `5` |
| gate settings | `{"speed": 0.5, "robot_length": 0.45, "margin": 0.5, "crowding_radius": 1.5, "crowding_max_robots": 1}` |
| maps | `{"narrow_aisle": "code/mrs-ethics/src/mrs_sim/worlds/narrow_aisle.yaml", "samples_world": "code/mrs-ethics/src/fleet_checker/test/fixtures/samples_world.yaml", "bench_grid": "code/mrs-ethics/benchmarks/maps/bench_grid.yaml"}` |
| timer | time.perf_counter_ns |
| percentiles | nearest rank |
| garbage collection | on |

## Method

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

## 1. Checker timing

### Per cell

Times in ms over all samples of a cell; findings and rechecks are means per check. The shares are of the cell's summed time.

| map | robots | density | samples | findings/check | rechecks/check | median | p95 | p99 | compile % | detection % | rechecks % | other % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| narrow_aisle | 2 | low | 1000 | 0.115 | 0.115 | 0.071 | 0.246 | 0.268 | 46% | 32% | 14% | 7.5% |
| narrow_aisle | 2 | medium | 1000 | 0.48 | 0.48 | 0.114 | 0.378 | 0.743 | 33% | 23% | 32% | 12% |
| narrow_aisle | 2 | high | 1000 | 1.28 | 1.28 | 0.347 | 0.713 | 0.765 | 23% | 16% | 47% | 14% |
| narrow_aisle | 4 | low | 1000 | 0.825 | 0.825 | 0.334 | 0.569 | 0.652 | 25% | 19% | 40% | 17% |
| narrow_aisle | 4 | medium | 1000 | 2.18 | 2.68 | 0.946 | 2.23 | 2.49 | 12% | 9.6% | 65% | 13% |
| narrow_aisle | 4 | high | 1000 | 2.85 | 5 | 2.08 | 2.87 | 3.36 | 8.3% | 6.2% | 74% | 11% |
| samples_world | 2 | low | 1000 | 0.07 | 0.07 | 0.072 | 0.249 | 0.301 | 49% | 32% | 13% | 5.4% |
| samples_world | 2 | medium | 1000 | 0.455 | 0.455 | 0.115 | 0.549 | 0.632 | 34% | 22% | 33% | 11% |
| samples_world | 2 | high | 1000 | 1.22 | 1.22 | 0.357 | 0.596 | 0.676 | 23% | 16% | 47% | 14% |
| samples_world | 4 | low | 1000 | 0.385 | 0.385 | 0.131 | 0.415 | 0.636 | 35% | 24% | 29% | 12% |
| samples_world | 4 | medium | 1000 | 1.56 | 1.98 | 0.761 | 1.92 | 2.22 | 16% | 11% | 61% | 13% |
| samples_world | 4 | high | 1000 | 1.93 | 3.29 | 1.27 | 2.00 | 2.25 | 12% | 7.9% | 68% | 12% |
| bench_grid | 2 | low | 1000 | 0.17 | 0.17 | 0.278 | 0.820 | 2.21 | 58% | 21% | 18% | 2.8% |
| bench_grid | 2 | medium | 1000 | 0.205 | 0.205 | 0.366 | 0.983 | 1.20 | 58% | 19% | 20% | 2.8% |
| bench_grid | 2 | high | 1000 | 0.21 | 0.21 | 0.421 | 1.04 | 2.43 | 60% | 18% | 19% | 2.4% |
| bench_grid | 4 | low | 1000 | 1.08 | 1.1 | 0.792 | 3.50 | 8.18 | 32% | 12% | 51% | 5.7% |
| bench_grid | 4 | medium | 1000 | 1.38 | 1.4 | 1.35 | 4.25 | 8.85 | 30% | 9.9% | 55% | 5.1% |
| bench_grid | 4 | high | 1000 | 2.21 | 2.25 | 1.96 | 8.80 | 15.7 | 22% | 7.2% | 66% | 4.7% |
| bench_grid | 8 | low | 1000 | 4.74 | 5 | 4.93 | 19.0 | 28.4 | 11% | 4.2% | 79% | 5.7% |
| bench_grid | 8 | medium | 1000 | 5.54 | 5.83 | 8.02 | 25.8 | 36.8 | 10% | 3.5% | 82% | 4.9% |
| bench_grid | 8 | high | 1000 | 9.39 | 10.2 | 15.9 | 50.0 | 73.3 | 6.3% | 2.2% | 87% | 4.4% |
| bench_grid | 16 | low | 1000 | 17.7 | 20.5 | 48.1 | 93.5 | 137 | 3.0% | 1.3% | 90% | 5.3% |
| bench_grid | 16 | medium | 1000 | 21.6 | 24.5 | 72.5 | 141 | 174 | 2.5% | 1.1% | 92% | 4.5% |
| bench_grid | 16 | high | 1000 | 31.7 | 38 | 147 | 260 | 314 | 1.7% | 0.8% | 94% | 4.0% |
| bench_grid | 32 | low | 1000 | 54.3 | 71.8 | 358 | 586 | 667 | 0.8% | 0.5% | 94% | 5.0% |
| bench_grid | 32 | medium | 1000 | 69.8 | 89.5 | 596 | 853 | 961 | 0.7% | 0.4% | 95% | 4.2% |
| bench_grid | 32 | high | 1000 | 94.5 | 133 | 1120 | 1503 | 1761 | 0.4% | 0.3% | 96% | 3.6% |

Medians, p95 and p99 of each part, per cell, are in `perf_summary.csv`; each scenario's in `perf_scenarios.csv`.

### Where the time goes

**narrow_aisle.** On narrow_aisle, from 2 robots on, rechecks take the largest share of the time: 68% at 4 robots.

- 2 robots: rechecks take 38% of the time (compile 29%, detection 20%, rechecks 38%, other constraint work 12%)
- 4 robots: rechecks take 68% of the time (compile 11%, detection 8%, rechecks 68%, other constraint work 12%)

**samples_world.** On samples_world, from 2 robots on, rechecks take the largest share of the time: 62% at 4 robots.

- 2 robots: rechecks take 38% of the time (compile 30%, detection 20%, rechecks 38%, other constraint work 12%)
- 4 robots: rechecks take 62% of the time (compile 15%, detection 10%, rechecks 62%, other constraint work 12%)

**bench_grid.** On bench_grid, from 4 robots on, rechecks take the largest share of the time: 95% at 32 robots. Below that, the largest share is compile at 2 robots.

- 2 robots: compile takes 59% of the time (compile 59%, detection 19%, rechecks 19%, other constraint work 3%)
- 4 robots: rechecks take 60% of the time (compile 27%, detection 9%, rechecks 60%, other constraint work 5%)
- 8 robots: rechecks take 84% of the time (compile 8%, detection 3%, rechecks 84%, other constraint work 5%)
- 16 robots: rechecks take 93% of the time (compile 2%, detection 1%, rechecks 93%, other constraint work 4%)
- 32 robots: rechecks take 95% of the time (compile 1%, detection 0%, rechecks 95%, other constraint work 4%)

### Rechecks against fleet size

On bench_grid, the densities pooled: rechecks are the `order_before` verifications of one check's verdict.

| robots | findings per check | rechecks per check | ms per recheck |
|---|---|---|---|
| 2 | 0.195 | 0.195 | 0.423 |
| 4 | 1.55 | 1.58 | 0.705 |
| 8 | 6.56 | 6.99 | 1.52 |
| 16 | 23.7 | 27.7 | 3.18 |
| 32 | 72.9 | 97.9 | 6.83 |

- Mean rechecks per check: 2 robots 0.195, 4 robots 1.585, 8 robots 6.9917, 16 robots 27.67, 32 robots 97.9383
- Each doubling of the fleet multiplies them by: 2 to 4 robots x8.13, 4 to 8 robots x4.41, 8 to 16 robots x3.96, 16 to 32 robots x3.54
- Least-squares slope of log(mean rechecks) on log(robots), over 5 fleet sizes: 2.21 (rechecks grow about as robots^2.21)
- Time per recheck: 0.4228 ms at 2 robots, 6.8291 ms at 32 robots

### S-2: the extra time of people

The same scenarios with and without the people; the extra time is each scenario's median with people less its median without, then over the scenarios (ms).

| map | robots | density | people | scenarios | S-2 findings per check | median without | median with | extra: median | extra: p95 | of it in detection | in the verdict | rechecks without / with |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench_grid | 8 | medium | 4 | 200 | 0.27 | 8.02 | 10.4 | 2.34 | 8.95 | 0.373 | 1.98 | 5.83 / 6.11 |
| bench_grid | 16 | medium | 4 | 200 | 0.895 | 72.5 | 89.5 | 17.7 | 33.3 | 0.705 | 17.1 | 24.5 / 25.5 |

### What the instruments cost

On bench_grid, 8 robots, medium: 200 pairs of the same scenario plain and instrumented. Instrumented less plain: median -0.013 ms (-0.1% of the plain median of 8.26 ms), p95 0.449 ms.

## 2. Repairability

### Outcomes

Per map and fleet size, the densities together; share [Wilson 95 %] (count). Each cell's are in `repair_summary.csv`, each scenario's in `repair_scenarios.csv`.

| map | robots | scenarios | first round | kept targets | changed targets | stuck | exhausted |
|---|---|---|---|---|---|---|---|
| narrow_aisle | 2 | 150 | 47% [40%–55%] (71) | 23% [17%–31%] (35) | 6.7% [3.7%–12%] (10) | 23% [17%–30%] (34) | 0.0% [0.0%–2.5%] (0) |
| narrow_aisle | 4 | 150 | 9.3% [5.6%–15%] (14) | 1.3% [0.4%–4.7%] (2) | 0.0% [0.0%–2.5%] (0) | 89% [83%–93%] (134) | 0.0% [0.0%–2.5%] (0) |
| samples_world | 2 | 150 | 49% [41%–57%] (74) | 13% [8.8%–20%] (20) | 1.3% [0.4%–4.7%] (2) | 36% [29%–44%] (54) | 0.0% [0.0%–2.5%] (0) |
| samples_world | 4 | 150 | 25% [19%–33%] (38) | 0.7% [0.1%–3.7%] (1) | 0.7% [0.1%–3.7%] (1) | 73% [66%–80%] (110) | 0.0% [0.0%–2.5%] (0) |
| bench_grid | 2 | 150 | 86% [80%–91%] (129) | 11% [6.7%–17%] (16) | 0.7% [0.1%–3.7%] (1) | 2.7% [1.0%–6.7%] (4) | 0.0% [0.0%–2.5%] (0) |
| bench_grid | 4 | 150 | 32% [25%–40%] (48) | 44% [36%–52%] (66) | 15% [10%–22%] (23) | 8.7% [5.1%–14%] (13) | 0.0% [0.0%–2.5%] (0) |
| bench_grid | 8 | 150 | 2.7% [1.0%–6.7%] (4) | 20% [14%–27%] (30) | 19% [13%–26%] (28) | 57% [49%–65%] (86) | 1.3% [0.4%–4.7%] (2) |
| bench_grid | 16 | 150 | 0.0% [0.0%–2.5%] (0) | 1.3% [0.4%–4.7%] (2) | 1.3% [0.4%–4.7%] (2) | 79% [71%–84%] (118) | 19% [13%–26%] (28) |
| bench_grid | 32 | 150 | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 23% [17%–30%] (34) | 77% [70%–83%] (116) |
| * | * | 1350 | 28% [26%–30%] (378) | 13% [11%–15%] (172) | 5.0% [3.9%–6.2%] (67) | 43% [41%–46%] (587) | 11% [9.3%–13%] (146) |

### Plans with a conflict in round 1

| map | robots | with a conflict | kept targets | changed targets | stuck | exhausted |
|---|---|---|---|---|---|---|
| narrow_aisle | 2 | 79 | 44% [34%–55%] (35) | 13% [7.0%–22%] (10) | 43% [33%–54%] (34) | 0.0% [0.0%–4.6%] (0) |
| narrow_aisle | 4 | 136 | 1.5% [0.4%–5.2%] (2) | 0.0% [0.0%–2.8%] (0) | 99% [95%–100%] (134) | 0.0% [0.0%–2.8%] (0) |
| samples_world | 2 | 76 | 26% [18%–37%] (20) | 2.6% [0.7%–9.1%] (2) | 71% [60%–80%] (54) | 0.0% [0.0%–4.8%] (0) |
| samples_world | 4 | 112 | 0.9% [0.2%–4.9%] (1) | 0.9% [0.2%–4.9%] (1) | 98% [94%–100%] (110) | 0.0% [0.0%–3.3%] (0) |
| bench_grid | 2 | 21 | 76% [55%–89%] (16) | 4.8% [0.9%–23%] (1) | 19% [7.7%–40%] (4) | 0.0% [0.0%–15%] (0) |
| bench_grid | 4 | 102 | 65% [55%–73%] (66) | 23% [16%–32%] (23) | 13% [7.6%–21%] (13) | 0.0% [0.0%–3.6%] (0) |
| bench_grid | 8 | 146 | 21% [15%–28%] (30) | 19% [14%–26%] (28) | 59% [51%–67%] (86) | 1.4% [0.4%–4.9%] (2) |
| bench_grid | 16 | 150 | 1.3% [0.4%–4.7%] (2) | 1.3% [0.4%–4.7%] (2) | 79% [71%–84%] (118) | 19% [13%–26%] (28) |
| bench_grid | 32 | 150 | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 23% [17%–30%] (34) | 77% [70%–83%] (116) |
| * | * | 972 | 18% [15%–20%] (172) | 6.9% [5.5%–8.7%] (67) | 60% [57%–63%] (587) | 15% [13%–17%] (146) |

### The round approved plans passed in

| round 1 | round 2 | round 3 | round 4 | round 5 |
|---|---|---|---|---|
| 378 | 164 | 44 | 27 | 4 |

### What the fake planner did with each kind of constraint

Over all scenarios: changed the plan, noted it (nothing to change; kept out of the robot's tasks from then on), or could not apply it.

| kind | rule | received | changed | noted | not applied |
|---|---|---|---|---|---|
| custom | * | 22085 | 0 | 0 | 22085 |
| forbid_node | * | 3269 | 3269 | 0 | 0 |
| order_before | * | 17131 | 17131 | 0 | 0 |
| custom | capacity_exceeded | 12 | 0 | 0 | 12 |
| custom | node_occupied | 22073 | 0 | 0 | 22073 |
| forbid_node | node_occupied | 3269 | 3269 | 0 | 0 |
| order_before | capacity_exceeded | 6304 | 6304 | 0 | 0 |
| order_before | node_occupied | 10827 | 10827 | 0 | 0 |

## Limitations

- One laptop process. Apple silicon has performance and efficiency cores, and macOS does not let
  a process be pinned to one kind; the run log has each cell's wall time.
- The times include the wrapping that splits them; its cost is measured above.
- Every plan has one task per robot and no `depends_on`, and every target is in an open area:
  the findings are S-1 and S-4 conflicts (and S-2 in its series), not P-1, P-2 or P-3.
- The small maps take two or four robots: each has six open-area nodes.
- S-2 is measured only in its series, with people standing still where they are drawn.
- Repairability is that of the fake planner, which applies constraints mechanically. It is not
  the success rate of an LLM planner.
