# Checker benchmarks, 2026-11-05

## Setup

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/diagnose_attempts.py run /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/attempts` |
| Date | 2026-11-05T11:19:26-08:00 |
| Code | git `c17bc6b3747ea3d6f8b8cdc24fde2a7b9cd6a594` |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | Battery Power |
| Kept awake by | caffeinate (pid 8551): PreventUserIdleSystemSleep for pid 8550 |
| Load average at the start (1, 5, 15 min) | 6.27, 5.09, 3.75 |
| Busiest other processes at the start (% CPU) | WindowServer 10; claude 8.3; Code Helper (Renderer) 4.7; Code Helper 2.4; Google Chrome Helper (Aperitif Renderer) 2.1 |
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
| narrow_aisle | 2 | 150 | 47% [40%–55%] (71) | 32% [25%–40%] (48) | 17% [12%–24%] (26) | 0.0% [0.0%–2.5%] (0) | 3.3% [1.4%–7.6%] (5) |
| narrow_aisle | 4 | 150 | 9.3% [5.6%–15%] (14) | 1.3% [0.4%–4.7%] (2) | 0.0% [0.0%–2.5%] (0) | 2.7% [1.0%–6.7%] (4) | 87% [80%–91%] (130) |
| samples_world | 2 | 150 | 49% [41%–57%] (74) | 17% [12%–23%] (25) | 15% [9.9%–21%] (22) | 15% [10%–22%] (23) | 4.0% [1.8%–8.5%] (6) |
| samples_world | 4 | 150 | 25% [19%–33%] (38) | 0.7% [0.1%–3.7%] (1) | 11% [7.2%–17%] (17) | 8.7% [5.1%–14%] (13) | 54% [46%–62%] (81) |
| bench_grid | 2 | 150 | 86% [80%–91%] (129) | 13% [8.3%–19%] (19) | 0.7% [0.1%–3.7%] (1) | 0.0% [0.0%–2.5%] (0) | 0.7% [0.1%–3.7%] (1) |
| bench_grid | 4 | 150 | 32% [25%–40%] (48) | 49% [41%–57%] (73) | 16% [11%–23%] (24) | 0.0% [0.0%–2.5%] (0) | 3.3% [1.4%–7.6%] (5) |
| bench_grid | 8 | 150 | 2.7% [1.0%–6.7%] (4) | 38% [31%–46%] (57) | 31% [24%–39%] (47) | 1.3% [0.4%–4.7%] (2) | 27% [20%–34%] (40) |
| bench_grid | 16 | 150 | 0.0% [0.0%–2.5%] (0) | 4.0% [1.8%–8.5%] (6) | 16% [11%–23%] (24) | 2.7% [1.0%–6.7%] (4) | 77% [70%–83%] (116) |
| bench_grid | 32 | 150 | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 100% [98%–100%] (150) |
| * | * | 1350 | 28% [26%–30%] (378) | 17% [15%–19%] (231) | 12% [10%–14%] (161) | 3.4% [2.6%–4.5%] (46) | 40% [37%–42%] (534) |

### Plans with a conflict in round 1

| map | robots | with a conflict | kept targets | changed targets | stuck | exhausted |
|---|---|---|---|---|---|---|
| narrow_aisle | 2 | 79 | 61% [50%–71%] (48) | 33% [24%–44%] (26) | 0.0% [0.0%–4.6%] (0) | 6.3% [2.7%–14%] (5) |
| narrow_aisle | 4 | 136 | 1.5% [0.4%–5.2%] (2) | 0.0% [0.0%–2.8%] (0) | 2.9% [1.1%–7.3%] (4) | 96% [91%–98%] (130) |
| samples_world | 2 | 76 | 33% [23%–44%] (25) | 29% [20%–40%] (22) | 30% [21%–41%] (23) | 7.9% [3.7%–16%] (6) |
| samples_world | 4 | 112 | 0.9% [0.2%–4.9%] (1) | 15% [9.7%–23%] (17) | 12% [6.9%–19%] (13) | 72% [63%–80%] (81) |
| bench_grid | 2 | 21 | 90% [71%–97%] (19) | 4.8% [0.9%–23%] (1) | 0.0% [0.0%–15%] (0) | 4.8% [0.9%–23%] (1) |
| bench_grid | 4 | 102 | 72% [62%–79%] (73) | 24% [16%–33%] (24) | 0.0% [0.0%–3.6%] (0) | 4.9% [2.1%–11%] (5) |
| bench_grid | 8 | 146 | 39% [32%–47%] (57) | 32% [25%–40%] (47) | 1.4% [0.4%–4.9%] (2) | 27% [21%–35%] (40) |
| bench_grid | 16 | 150 | 4.0% [1.8%–8.5%] (6) | 16% [11%–23%] (24) | 2.7% [1.0%–6.7%] (4) | 77% [70%–83%] (116) |
| bench_grid | 32 | 150 | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 0.0% [0.0%–2.5%] (0) | 100% [98%–100%] (150) |
| * | * | 972 | 24% [21%–27%] (231) | 17% [14%–19%] (161) | 4.7% [3.6%–6.2%] (46) | 55% [52%–58%] (534) |

### The round approved plans passed in

| round 1 | round 2 | round 3 | round 4 | round 5 |
|---|---|---|---|---|
| 378 | 183 | 60 | 98 | 51 |

### What the fake planner did with each kind of constraint

Over all scenarios: changed the plan, noted it (nothing to change; kept out of the robot's tasks from then on), or could not apply it.

| kind | rule | received | changed | noted | not applied |
|---|---|---|---|---|---|
| avoid_node | * | 165 | 53 | 110 | 2 |
| custom | * | 5160 | 0 | 0 | 5160 |
| forbid_node | * | 10691 | 9941 | 744 | 6 |
| order_before | * | 19596 | 19596 | 0 | 0 |
| avoid_node | region_access_denied | 165 | 53 | 110 | 2 |
| custom | capacity_exceeded | 452 | 0 | 0 | 452 |
| custom | node_occupied | 4708 | 0 | 0 | 4708 |
| forbid_node | final_stop_in_passthrough | 333 | 177 | 156 | 0 |
| forbid_node | node_occupied | 10358 | 9764 | 588 | 6 |
| order_before | capacity_exceeded | 6493 | 6493 | 0 | 0 |
| order_before | node_occupied | 13103 | 13103 | 0 | 0 |

### S-4's constraints by situation

Over all scenarios, the situation each S-4 constraint answers (`VerdictContent.cases`; every rule's are in `repair_cases.csv`).

| situation | kind | received | changed | noted | not applied |
|---|---|---|---|---|---|
| `yielder_waits`: the yielder waits until the keeper has left (verified) | order_before | 12068 | 12068 | 0 | 0 |
| `keeper_in_the_way`: - | forbid_node | 5843 | 5325 | 516 | 2 |
| `keeper_ends_elsewhere`: the keeper, there to the end of its plan, cannot wait: it ends somewhere else | forbid_node | 3372 | 3368 | 0 | 4 |
| `cycle`: an ordering that, with the verdict's other orderings, would close a cycle | custom | 2781 | 0 | 0 | 2781 |
| `wait_nearby`: a task of the yielder ends there, and waiting for the keeper does not help | custom | 1557 | 0 | 0 | 1557 |
| `keeper_waits`: the keeper, there to the end of its plan, waits until the yielder has gone by (verified) | order_before | 1035 | 1035 | 0 | 0 |
| `from_start`: the yielder is at the node when its plan starts | forbid_node | 659 | 608 | 51 | 0 |
| `no_waiting_there`: the yielder only waits there between tasks: it waits somewhere else | forbid_node | 483 | 462 | 21 | 0 |
| `passes_waiting_fails`: the yielder drives through, and waiting for the keeper does not help | custom | 353 | 0 | 0 | 353 |
| `from_start`: the yielder is at the node when its plan starts | custom | 17 | 0 | 0 | 17 |
| `cannot_be_done`: the keeper stays there to the end of its plan, where a task of the yielder ends | forbid_node | 1 | 1 | 0 | 0 |

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
