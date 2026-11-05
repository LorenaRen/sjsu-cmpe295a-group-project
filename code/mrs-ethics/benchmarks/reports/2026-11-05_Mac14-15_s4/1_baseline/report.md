# Checker benchmarks, 2026-11-05

## Setup

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/wt_step1/code/mrs-ethics/benchmarks/run.py --only repair --out /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/s4_runs/1_baseline` |
| Date | 2026-11-05T01:45:31-08:00 |
| Code | git `d64457f74ef7ce65865bac6a36bc14ab1286d573` |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Kept awake by | caffeinate (pid 95032): PreventUserIdleSystemSleep for pid 95031 |
| Load average at the start (1, 5, 15 min) | 2.15, 2.02, 2 |
| Busiest other processes at the start (% CPU) | Python 33.3; XprotectService 14.2; fileproviderd 12.1; WindowServer 10.1; Code Helper (Renderer) 7.8 |
| Python | CPython 3.14.4 (`/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python`), pydantic 2.14.1, PYTHONHASHSEED=0 |
| Checker source | `/private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/wt_step1/code/mrs-ethics/src`, git `d64457f74ef7ce65865bac6a36bc14ab1286d573` |

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

### S-4's constraints by situation

Over all scenarios, the situation each S-4 constraint answers (`VerdictContent.cases`; every rule's are in `repair_cases.csv`).

| situation | kind | received | changed | noted | not applied |
|---|---|---|---|---|---|
| `passes_waiting_fails`: the yielder drives through, and waiting for the keeper does not help | custom | 10450 | 0 | 0 | 10450 |
| `yielder_waits`: the yielder waits until the keeper has left (verified) | order_before | 9756 | 9756 | 0 | 0 |
| `from_start`: the yielder is at the node when its plan starts | custom | 4655 | 0 | 0 | 4655 |
| `keeper_ends_elsewhere`: the keeper, there to the end of its plan, cannot wait: it ends somewhere else | forbid_node | 3269 | 3269 | 0 | 0 |
| `wait_nearby`: a task of the yielder ends there, and waiting for the keeper does not help | custom | 3157 | 0 | 0 | 3157 |
| `cycle`: an ordering that, with the verdict's other orderings, would close a cycle | custom | 2151 | 0 | 0 | 2151 |
| `cannot_be_done`: the keeper stays there to the end of its plan, where a task of the yielder ends | custom | 1660 | 0 | 0 | 1660 |
| `keeper_waits`: the keeper, there to the end of its plan, waits until the yielder has gone by (verified) | order_before | 1071 | 1071 | 0 | 0 |

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
