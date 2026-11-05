# Checker benchmarks, 2026-11-05

## Setup

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/diagnose_attempts.py run /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/attempts` |
| Date | 2026-11-05T11:20:34-08:00 |
| Code | git `c17bc6b3747ea3d6f8b8cdc24fde2a7b9cd6a594` |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | Battery Power |
| Kept awake by | caffeinate (pid 8551): PreventUserIdleSystemSleep for pid 8550 |
| Load average at the start (1, 5, 15 min) | 6.01, 5.11, 3.84 |
| Busiest other processes at the start (% CPU) | Claude Helper (Renderer) 107.4; Code Helper (Renderer) 61.3; WindowServer 7.4; Google Chrome 3.4; locationd 2.4 |
| Python | CPython 3.14.4 (`/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python`), pydantic 2.14.1, PYTHONHASHSEED=0 |
| Checker source | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/src`, git `c17bc6b3747ea3d6f8b8cdc24fde2a7b9cd6a594` |

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
| repair: max attempts | `10` |
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
| narrow_aisle | 2 | 150 | 47% [40%–55%] (71) | 32% [25%–40%] (48) | 17% [12%–24%] (26) | 0.0% [0.0%–2.5%] (0) | 3.3% [1.4%–7.6%] (5) |
| narrow_aisle | 4 | 150 | 9.3% [5.6%–15%] (14) | 1.3% [0.4%–4.7%] (2) | 0.0% [0.0%–2.5%] (0) | 21% [15%–28%] (31) | 69% [61%–76%] (103) |
| samples_world | 2 | 150 | 49% [41%–57%] (74) | 17% [12%–23%] (25) | 19% [13%–26%] (28) | 15% [10%–22%] (23) | 0.0% [0.0%–2.5%] (0) |
| samples_world | 4 | 150 | 25% [19%–33%] (38) | 0.7% [0.1%–3.7%] (1) | 13% [8.8%–20%] (20) | 23% [17%–31%] (35) | 37% [30%–45%] (56) |
| bench_grid | 2 | 150 | 86% [80%–91%] (129) | 13% [8.3%–19%] (19) | 1.3% [0.4%–4.7%] (2) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) |
| bench_grid | 4 | 150 | 32% [25%–40%] (48) | 49% [41%–57%] (73) | 19% [14%–26%] (29) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) |
| bench_grid | 8 | 150 | 2.7% [1.0%–6.7%] (4) | 40% [32%–48%] (60) | 52% [44%–60%] (78) | 1.3% [0.4%–4.7%] (2) | 4.0% [1.8%–8.5%] (6) |
| bench_grid | 16 | 150 | 0.0% [0.0%–2.5%] (0) | 6.0% [3.2%–11%] (9) | 55% [47%–62%] (82) | 2.7% [1.0%–6.7%] (4) | 37% [29%–45%] (55) |
| bench_grid | 32 | 150 | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 100% [98%–100%] (150) |
| * | * | 1350 | 28% [26%–30%] (378) | 18% [16%–20%] (237) | 20% [18%–22%] (265) | 7.0% [5.8%–8.5%] (95) | 28% [25%–30%] (375) |

### Plans with a conflict in round 1

| map | robots | with a conflict | kept targets | changed targets | stuck | exhausted |
|---|---|---|---|---|---|---|
| narrow_aisle | 2 | 79 | 61% [50%–71%] (48) | 33% [24%–44%] (26) | 0.0% [0.0%–4.6%] (0) | 6.3% [2.7%–14%] (5) |
| narrow_aisle | 4 | 136 | 1.5% [0.4%–5.2%] (2) | 0.0% [0.0%–2.8%] (0) | 23% [17%–31%] (31) | 76% [68%–82%] (103) |
| samples_world | 2 | 76 | 33% [23%–44%] (25) | 37% [27%–48%] (28) | 30% [21%–41%] (23) | 0.0% [0.0%–4.8%] (0) |
| samples_world | 4 | 112 | 0.9% [0.2%–4.9%] (1) | 18% [12%–26%] (20) | 31% [23%–40%] (35) | 50% [41%–59%] (56) |
| bench_grid | 2 | 21 | 90% [71%–97%] (19) | 9.5% [2.6%–29%] (2) | 0.0% [0.0%–15%] (0) | 0.0% [0.0%–15%] (0) |
| bench_grid | 4 | 102 | 72% [62%–79%] (73) | 28% [21%–38%] (29) | 0.0% [0.0%–3.6%] (0) | 0.0% [0.0%–3.6%] (0) |
| bench_grid | 8 | 146 | 41% [33%–49%] (60) | 53% [45%–61%] (78) | 1.4% [0.4%–4.9%] (2) | 4.1% [1.9%–8.7%] (6) |
| bench_grid | 16 | 150 | 6.0% [3.2%–11%] (9) | 55% [47%–62%] (82) | 2.7% [1.0%–6.7%] (4) | 37% [29%–45%] (55) |
| bench_grid | 32 | 150 | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 100% [98%–100%] (150) |
| * | * | 972 | 24% [22%–27%] (237) | 27% [25%–30%] (265) | 9.8% [8.1%–12%] (95) | 39% [36%–42%] (375) |

### The round approved plans passed in

| round 1 | round 2 | round 3 | round 4 | round 5 | round 6 | round 7 | round 8 | round 9 | round 10 |
|---|---|---|---|---|---|---|---|---|---|
| 378 | 183 | 60 | 98 | 51 | 45 | 22 | 21 | 14 | 8 |

### What the fake planner did with each kind of constraint

Over all scenarios: changed the plan, noted it (nothing to change; kept out of the robot's tasks from then on), or could not apply it.

| kind | rule | received | changed | noted | not applied |
|---|---|---|---|---|---|
| avoid_node | * | 255 | 62 | 170 | 23 |
| custom | * | 7298 | 0 | 0 | 7298 |
| forbid_node | * | 16112 | 15255 | 798 | 59 |
| order_before | * | 23680 | 23680 | 0 | 0 |
| avoid_node | region_access_denied | 255 | 62 | 170 | 23 |
| custom | capacity_exceeded | 1223 | 0 | 0 | 1223 |
| custom | node_occupied | 6075 | 0 | 0 | 6075 |
| forbid_node | final_stop_in_passthrough | 369 | 194 | 167 | 8 |
| forbid_node | node_occupied | 15743 | 15061 | 631 | 51 |
| order_before | capacity_exceeded | 6894 | 6894 | 0 | 0 |
| order_before | node_occupied | 16786 | 16786 | 0 | 0 |

### S-4's constraints by situation

Over all scenarios, the situation each S-4 constraint answers (`VerdictContent.cases`; every rule's are in `repair_cases.csv`).

| situation | kind | received | changed | noted | not applied |
|---|---|---|---|---|---|
| `yielder_waits`: the yielder waits until the keeper has left (verified) | order_before | 15706 | 15706 | 0 | 0 |
| `keeper_in_the_way`: - | forbid_node | 9347 | 8777 | 549 | 21 |
| `keeper_ends_elsewhere`: the keeper, there to the end of its plan, cannot wait: it ends somewhere else | forbid_node | 3677 | 3649 | 0 | 28 |
| `cycle`: an ordering that, with the verdict's other orderings, would close a cycle | custom | 3028 | 0 | 0 | 3028 |
| `wait_nearby`: a task of the yielder ends there, and waiting for the keeper does not help | custom | 2166 | 0 | 0 | 2166 |
| `no_waiting_there`: the yielder only waits there between tasks: it waits somewhere else | forbid_node | 1697 | 1667 | 30 | 0 |
| `keeper_waits`: the keeper, there to the end of its plan, waits until the yielder has gone by (verified) | order_before | 1080 | 1080 | 0 | 0 |
| `from_start`: the yielder is at the node when its plan starts | forbid_node | 1020 | 966 | 52 | 2 |
| `passes_waiting_fails`: the yielder drives through, and waiting for the keeper does not help | custom | 863 | 0 | 0 | 863 |
| `from_start`: the yielder is at the node when its plan starts | custom | 18 | 0 | 0 | 18 |
| `cannot_be_done`: the keeper stays there to the end of its plan, where a task of the yielder ends | forbid_node | 2 | 2 | 0 | 0 |

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
