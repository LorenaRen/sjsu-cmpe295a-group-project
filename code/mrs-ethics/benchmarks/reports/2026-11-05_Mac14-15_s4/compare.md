# S-4's custom constraints: the repair benchmark before and after

Same 1350 scenarios in every run (manifest-checked), at most 5 attempts. Share [Wilson 95 %] (count). Computed by `compare_repair.py` from the runs' CSV files; each run's own report is beside it.

## All scenarios

| outcome | baseline (n = 1350) | + fake planner (n = 1350) | + checker (n = 1350) |
|---|---|---|---|
| approved in round 1 | 28.0% [25.7%–30.4%] (378) | 28.0% [25.7%–30.4%] (378) | 28.0% [25.7%–30.4%] (378) |
| repaired, targets kept | 12.7% [11.1%–14.6%] (172) | 12.7% [11.1%–14.6%] (172) | 17.1% [15.2%–19.2%] (231) |
| repaired, a target moved | 5.0% [3.9%–6.2%] (67) | 8.6% [7.2%–10.2%] (116) | 11.9% [10.3%–13.8%] (161) |
| stuck | 43.5% [40.9%–46.1%] (587) | 37.0% [34.4%–39.6%] (499) | 3.4% [2.6%–4.5%] (46) |
| out of attempts | 10.8% [9.3%–12.6%] (146) | 13.7% [12.0%–15.6%] (185) | 39.6% [37.0%–42.2%] (534) |

## Scenarios with a conflict in round 1

| outcome | baseline (n = 972) | + fake planner (n = 972) | + checker (n = 972) |
|---|---|---|---|
| repaired, targets kept | 17.7% [15.4%–20.2%] (172) | 17.7% [15.4%–20.2%] (172) | 23.8% [21.2%–26.5%] (231) |
| repaired, a target moved | 6.9% [5.5%–8.7%] (67) | 11.9% [10.0%–14.1%] (116) | 16.6% [14.4%–19.0%] (161) |
| stuck | 60.4% [57.3%–63.4%] (587) | 51.3% [48.2%–54.5%] (499) | 4.7% [3.6%–6.2%] (46) |
| out of attempts | 15.0% [12.9%–17.4%] (146) | 19.0% [16.7%–21.6%] (185) | 54.9% [51.8%–58.0%] (534) |

## Stuck, by fleet size (bench_grid)

| robots | baseline | + fake planner | + checker |
|---|---|---|---|
| 2 | 2.7% [1.0%–6.7%] (4) | 2.7% [1.0%–6.7%] (4) | 0.0% [0.0%–2.5%] (0) |
| 4 | 8.7% [5.1%–14.3%] (13) | 8.7% [5.1%–14.3%] (13) | 0.0% [0.0%–2.5%] (0) |
| 8 | 57.3% [49.3%–65.0%] (86) | 54.7% [46.7%–62.4%] (82) | 1.3% [0.4%–4.7%] (2) |
| 16 | 78.7% [71.4%–84.5%] (118) | 78.0% [70.7%–83.9%] (117) | 2.7% [1.0%–6.7%] (4) |
| 32 | 22.7% [16.7%–30.0%] (34) | 22.7% [16.7%–30.0%] (34) | 0.0% [0.0%–2.5%] (0) |

## Rounds

Mean and median rounds of the repaired scenarios (kept or changed targets).

| run | repaired | mean rounds | median rounds |
|---|---|---|---|
| baseline | 239 | 2.46 | 2 |
| + fake planner | 288 | 2.60 | 2 |
| + checker | 392 | 3.04 | 3 |

## Constraints by kind

Over all scenarios: received (changed / noted / not applied).

| kind | rule | baseline | + fake planner | + checker |
|---|---|---|---|---|
| avoid_node | region_access_denied | - | 162 (54 / 108 / 0) | 165 (53 / 110 / 2) |
| custom | capacity_exceeded | 12 (0 / 0 / 12) | 118 (0 / 0 / 118) | 452 (0 / 0 / 452) |
| custom | node_occupied | 22073 (0 / 0 / 22073) | 20139 (0 / 0 / 20139) | 4708 (0 / 0 / 4708) |
| forbid_node | final_stop_in_passthrough | - | 316 (170 / 146 / 0) | 333 (177 / 156 / 0) |
| forbid_node | node_occupied | 3269 (3269 / 0 / 0) | 3200 (3200 / 0 / 0) | 10358 (9764 / 588 / 6) |
| order_before | capacity_exceeded | 6304 (6304 / 0 / 0) | 6305 (6305 / 0 / 0) | 6493 (6493 / 0 / 0) |
| order_before | node_occupied | 10827 (10827 / 0 / 0) | 10832 (10832 / 0 / 0) | 13103 (13103 / 0 / 0) |

## S-4's constraints by situation

Over all scenarios: received (changed / noted / not applied), by `VerdictContent.cases` and kind.

| situation | kind | baseline | + fake planner | + checker |
|---|---|---|---|---|
| `cannot_be_done` | custom | 1660 (0 / 0 / 1660) | - | - |
| `cannot_be_done` | forbid_node | - | - | 1 (1 / 0 / 0) |
| `cycle` | custom | 2151 (0 / 0 / 2151) | 2151 (0 / 0 / 2151) | 2781 (0 / 0 / 2781) |
| `from_start` | custom | 4655 (0 / 0 / 4655) | 4855 (0 / 0 / 4855) | 17 (0 / 0 / 17) |
| `from_start` | forbid_node | - | - | 659 (608 / 51 / 0) |
| `keeper_ends_elsewhere` | forbid_node | 3269 (3269 / 0 / 0) | 3200 (3200 / 0 / 0) | 3372 (3368 / 0 / 4) |
| `keeper_in_the_way` | forbid_node | - | - | 5843 (5325 / 516 / 2) |
| `keeper_waits` | order_before | 1071 (1071 / 0 / 0) | 1053 (1053 / 0 / 0) | 1035 (1035 / 0 / 0) |
| `no_waiting_there` | forbid_node | - | - | 483 (462 / 21 / 0) |
| `passes_waiting_fails` | custom | 10450 (0 / 0 / 10450) | 10052 (0 / 0 / 10052) | 353 (0 / 0 / 353) |
| `wait_nearby` | custom | 3157 (0 / 0 / 3157) | 3081 (0 / 0 / 3081) | 1557 (0 / 0 / 1557) |
| `yielder_waits` | order_before | 9756 (9756 / 0 / 0) | 9779 (9779 / 0 / 0) | 12068 (12068 / 0 / 0) |
