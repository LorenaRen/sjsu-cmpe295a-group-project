# Checker comparison, 2026-11-05

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old c93795c --new 65acde1 --cell bench_grid/8/medium/0 --cell bench_grid/16/medium/0 --cell bench_grid/32/medium/0 --out /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/s4_runs/timing_vs_main` |
| Kept awake by | caffeinate (pid 525): PreventUserIdleSystemSleep for pid 523 |
| Old checker | `c93795c7af5c406aba587617a5f2a85c016a827b` (c93795c) |
| New checker | `65acde1b1a07ef9556261079ca3a9f552fe72de8` (65acde1) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 1.78, 2.07, 2.09 |
| Busiest other processes at the start (% CPU) | fileproviderd 23; fseventsd 5.5; dasd 4.1; runningboardd 1.5; mds_stores 1.4 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (600 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench_grid | 8 | medium | 0 | 2.66 | 2.65 | 1.002 | 6.22 | 6.20 | 1.004 | 9.03 | 9.16 | 0.986 | 1.05 | 1.04 |
| bench_grid | 16 | medium | 0 | 15.3 | 15.5 | 0.985 | 29.4 | 29.8 | 0.989 | 34.8 | 35.4 | 0.983 | 10.2 | 10.5 |
| bench_grid | 32 | medium | 0 | 128 | 129 | 0.987 | 190 | 192 | 0.988 | 211 | 212 | 0.994 | 104 | 105 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| bench_grid/8/medium/0 | old | 1 | 4.1 | 1.78 | 1.72 |
| bench_grid/8/medium/0 | new | 2 | 4.1 | 1.72 | 1.74 |
| bench_grid/16/medium/0 | new | 1 | 18.0 | 1.74 | 2.03 |
| bench_grid/16/medium/0 | old | 2 | 17.8 | 2.03 | 1.95 |
| bench_grid/32/medium/0 | old | 1 | 133.0 | 1.95 | 1.94 |
| bench_grid/32/medium/0 | new | 2 | 134.4 | 1.94 | 2.26 |
