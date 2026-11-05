# Checker comparison, 2026-11-04

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old 969d5a8 --new f88f03b --cell bench_grid/8/low/0 --cell bench_grid/8/medium/0 --cell bench_grid/8/high/0 --cell bench_grid/16/low/0 --cell bench_grid/16/medium/0 --cell bench_grid/16/high/0 --out /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/r1_compare_rerun` |
| Kept awake by | caffeinate (pid 84911): PreventUserIdleSystemSleep for pid 84910 |
| Old checker | `969d5a8b3037b12eabc1db6ac1c0ddde0333b022` (969d5a8) |
| New checker | `f88f03bf7bd62df170b162508903f7874e5efac4` (f88f03b) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 1.7, 1.88, 2.2 |
| Busiest other processes at the start (% CPU) | XprotectService 15.5; Code Helper (Renderer) 3.9; WindowServer 3.4; syspolicyd 2.1; trustd 1.7 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (1200 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench_grid | 8 | low | 0 | 1.94 | 1.92 | 1.01 | 5.96 | 5.46 | 1.092 | 7.32 | 6.96 | 1.052 | 0.633 | 0.619 |
| bench_grid | 8 | medium | 0 | 2.68 | 2.67 | 1.004 | 6.36 | 6.43 | 0.989 | 9.34 | 9.16 | 1.02 | 1.06 | 1.06 |
| bench_grid | 8 | high | 0 | 4.29 | 4.25 | 1.01 | 11.2 | 10.9 | 1.026 | 18.7 | 17.6 | 1.063 | 2.21 | 2.18 |
| bench_grid | 16 | low | 0 | 11.9 | 11.3 | 1.051 | 22.8 | 21.6 | 1.057 | 32.3 | 30.3 | 1.066 | 7.92 | 7.38 |
| bench_grid | 16 | medium | 0 | 16.5 | 15.6 | 1.059 | 31.4 | 30.2 | 1.041 | 38.3 | 35.2 | 1.086 | 11.3 | 10.5 |
| bench_grid | 16 | high | 0 | 31.0 | 28.0 | 1.108 | 60.6 | 53.5 | 1.133 | 72.7 | 62.9 | 1.156 | 24.0 | 21.0 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| bench_grid/8/low/0 | old | 1 | 3.5 | 1.7 | 1.72 |
| bench_grid/8/low/0 | new | 2 | 3.4 | 1.72 | 1.74 |
| bench_grid/8/medium/0 | new | 1 | 4.1 | 1.74 | 1.92 |
| bench_grid/8/medium/0 | old | 2 | 4.1 | 1.92 | 1.92 |
| bench_grid/8/high/0 | old | 1 | 6.4 | 1.92 | 1.85 |
| bench_grid/8/high/0 | new | 2 | 6.3 | 1.85 | 1.95 |
| bench_grid/16/low/0 | new | 1 | 13.6 | 1.95 | 1.96 |
| bench_grid/16/low/0 | old | 2 | 14.4 | 1.96 | 1.96 |
| bench_grid/16/medium/0 | old | 1 | 19.1 | 1.96 | 2.05 |
| bench_grid/16/medium/0 | new | 2 | 18.0 | 2.05 | 2.18 |
| bench_grid/16/high/0 | new | 1 | 31.7 | 2.18 | 2.38 |
| bench_grid/16/high/0 | old | 2 | 35.0 | 2.38 | 2.26 |
