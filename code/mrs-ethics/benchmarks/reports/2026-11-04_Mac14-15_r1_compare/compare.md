# Checker comparison, 2026-11-04

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old 969d5a8 --new f88f03b --cell bench_grid/8/low/0 --cell bench_grid/8/medium/0 --cell bench_grid/8/high/0 --cell bench_grid/16/low/0 --cell bench_grid/16/medium/0 --cell bench_grid/16/high/0 --cell bench_grid/32/low/0 --cell bench_grid/32/medium/0 --cell bench_grid/32/high/0 --out code/mrs-ethics/benchmarks/reports/2026-11-04_Mac14-15_r1_compare` |
| Kept awake by | caffeinate (pid 84009): PreventUserIdleSystemSleep for pid 84008 |
| Old checker | `969d5a8b3037b12eabc1db6ac1c0ddde0333b022` (969d5a8) |
| New checker | `f88f03bf7bd62df170b162508903f7874e5efac4` (f88f03b) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 1.96, 2.4, 2.44 |
| Busiest other processes at the start (% CPU) | fileproviderd 30.8; Code Helper (Renderer) 14.1; WindowServer 5.4; Finder 5.3; bird 4.3 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (1800 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench_grid | 8 | low | 0 | 1.97 | 1.95 | 1.009 | 5.81 | 5.83 | 0.995 | 7.21 | 7.16 | 1.006 | 0.629 | 0.638 |
| bench_grid | 8 | medium | 0 | 2.68 | 2.69 | 0.997 | 6.41 | 6.31 | 1.015 | 9.51 | 9.30 | 1.022 | 1.07 | 1.07 |
| bench_grid | 8 | high | 0 | 4.26 | 4.21 | 1.014 | 11.2 | 10.8 | 1.036 | 18.4 | 17.9 | 1.03 | 2.20 | 2.16 |
| bench_grid | 16 | low | 0 | 12.2 | 11.2 | 1.09 | 25.0 | 21.6 | 1.16 | 35.9 | 29.8 | 1.205 | 8.06 | 7.28 |
| bench_grid | 16 | medium | 0 | 16.5 | 15.3 | 1.077 | 31.7 | 29.3 | 1.082 | 38.6 | 34.9 | 1.106 | 11.3 | 10.3 |
| bench_grid | 16 | high | 0 | 30.6 | 27.7 | 1.103 | 60.8 | 52.3 | 1.163 | 72.2 | 62.9 | 1.147 | 23.6 | 20.8 |
| bench_grid | 32 | low | 0 | 105 | 85.4 | 1.226 | 174 | 137 | 1.272 | 203 | 161 | 1.263 | 85.8 | 66.4 |
| bench_grid | 32 | medium | 0 | 162 | 128 | 1.263 | 249 | 190 | 1.311 | 282 | 213 | 1.328 | 138 | 104 |
| bench_grid | 32 | high | 0 | 317 | 241 | 1.314 | 448 | 335 | 1.34 | 511 | 374 | 1.365 | 280 | 205 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| bench_grid/8/low/0 | old | 1 | 3.5 | 1.96 | 2.13 |
| bench_grid/8/low/0 | new | 2 | 3.5 | 2.13 | 3.24 |
| bench_grid/8/medium/0 | new | 1 | 4.2 | 3.24 | 3.06 |
| bench_grid/8/medium/0 | old | 2 | 4.2 | 3.06 | 3.06 |
| bench_grid/8/high/0 | old | 1 | 6.4 | 3.06 | 6.65 |
| bench_grid/8/high/0 | new | 2 | 6.3 | 6.65 | 6.28 |
| bench_grid/16/low/0 | new | 1 | 13.6 | 6.28 | 5.74 |
| bench_grid/16/low/0 | old | 2 | 14.8 | 5.74 | 5.05 |
| bench_grid/16/medium/0 | old | 1 | 19.1 | 5.05 | 4.37 |
| bench_grid/16/medium/0 | new | 2 | 17.9 | 4.37 | 6.68 |
| bench_grid/16/high/0 | new | 1 | 31.3 | 6.68 | 4.94 |
| bench_grid/16/high/0 | old | 2 | 34.5 | 4.94 | 3.8 |
| bench_grid/32/low/0 | old | 1 | 111.2 | 3.8 | 2.42 |
| bench_grid/32/low/0 | new | 2 | 91.2 | 2.42 | 2.6 |
| bench_grid/32/medium/0 | new | 1 | 133.5 | 2.6 | 2.15 |
| bench_grid/32/medium/0 | old | 2 | 169.3 | 2.15 | 1.92 |
| bench_grid/32/high/0 | old | 1 | 327.8 | 1.92 | 2.01 |
| bench_grid/32/high/0 | new | 2 | 248.1 | 2.01 | 1.8 |
