# Checker comparison, 2026-11-05

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old c93795c --new 65acde1 --cell bench_grid/16/medium/0 --cell bench_grid/32/medium/0 --out /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/s4_runs/timing_vs_main_rerun` |
| Kept awake by | caffeinate (pid 895): PreventUserIdleSystemSleep for pid 893 |
| Old checker | `c93795c7af5c406aba587617a5f2a85c016a827b` (c93795c) |
| New checker | `65acde1b1a07ef9556261079ca3a9f552fe72de8` (65acde1) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 1.9, 2.03, 2.06 |
| Busiest other processes at the start (% CPU) | WindowServer 1.3; sysmond 1.2; opendirectoryd 0.9; syspolicyd 0.9; Code Helper (Renderer) 0.7 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (400 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench_grid | 16 | medium | 0 | 15.4 | 15.4 | 0.998 | 29.4 | 29.5 | 0.997 | 34.9 | 35.2 | 0.994 | 10.3 | 10.4 |
| bench_grid | 32 | medium | 0 | 128 | 129 | 0.989 | 191 | 192 | 0.99 | 222 | 213 | 1.041 | 104 | 106 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| bench_grid/16/medium/0 | old | 1 | 17.8 | 1.9 | 2.06 |
| bench_grid/16/medium/0 | new | 2 | 18.0 | 2.06 | 2.05 |
| bench_grid/32/medium/0 | new | 1 | 134.6 | 2.05 | 2.02 |
| bench_grid/32/medium/0 | old | 2 | 133.4 | 2.02 | 1.81 |
