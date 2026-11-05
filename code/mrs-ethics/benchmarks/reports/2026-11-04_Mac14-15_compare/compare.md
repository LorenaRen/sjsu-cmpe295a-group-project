# Checker comparison, 2026-11-04

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old bb46d18 --new 120a928 --out code/mrs-ethics/benchmarks/reports/2026-11-04_Mac14-15_compare` |
| Started from | /bin/zsh |
| Old checker | `bb46d185a4718fd248645ccaeff7f404c9bbece8` (bb46d18) |
| New checker | `120a928b8e01a567b8d303abeab59c699f5a8515` (120a928) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 1.8, 1.9, 2.02 |
| Busiest other processes at the start (% CPU) | fseventsd 10.7; mds_stores 2.6; mds 2; runningboardd 1.3; Code Helper 0.8 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (5800 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| narrow_aisle | 2 | low | 0 | 0.068 | 0.076 | 0.896 | 0.233 | 0.222 | 1.052 | 0.250 | 0.237 | 1.056 | 0.000 | 0.000 |
| narrow_aisle | 2 | medium | 0 | 0.105 | 0.113 | 0.935 | 0.355 | 0.297 | 1.197 | 0.739 | 0.554 | 1.334 | 0.000 | 0.000 |
| narrow_aisle | 2 | high | 0 | 0.338 | 0.287 | 1.179 | 0.700 | 0.500 | 1.398 | 0.743 | 0.564 | 1.316 | 0.147 | 0.092 |
| narrow_aisle | 4 | low | 0 | 0.332 | 0.285 | 1.162 | 0.568 | 0.452 | 1.257 | 0.634 | 0.468 | 1.356 | 0.135 | 0.080 |
| narrow_aisle | 4 | medium | 0 | 0.931 | 0.645 | 1.443 | 2.17 | 1.36 | 1.601 | 2.37 | 1.45 | 1.637 | 0.582 | 0.296 |
| narrow_aisle | 4 | high | 0 | 1.95 | 1.21 | 1.606 | 2.70 | 1.62 | 1.671 | 2.81 | 1.85 | 1.515 | 1.44 | 0.705 |
| samples_world | 2 | low | 0 | 0.071 | 0.077 | 0.916 | 0.244 | 0.223 | 1.098 | 0.284 | 0.261 | 1.089 | 0.000 | 0.000 |
| samples_world | 2 | medium | 0 | 0.107 | 0.116 | 0.927 | 0.534 | 0.434 | 1.23 | 0.572 | 0.498 | 1.147 | 0.000 | 0.000 |
| samples_world | 2 | high | 0 | 0.339 | 0.289 | 1.172 | 0.554 | 0.445 | 1.245 | 0.575 | 0.468 | 1.229 | 0.148 | 0.093 |
| samples_world | 4 | low | 0 | 0.124 | 0.135 | 0.919 | 0.389 | 0.328 | 1.186 | 0.615 | 0.489 | 1.256 | 0.000 | 0.000 |
| samples_world | 4 | medium | 0 | 0.731 | 0.532 | 1.374 | 1.88 | 1.16 | 1.611 | 2.23 | 1.25 | 1.793 | 0.410 | 0.217 |
| samples_world | 4 | high | 0 | 1.22 | 0.810 | 1.502 | 1.94 | 1.23 | 1.569 | 2.17 | 1.35 | 1.614 | 0.827 | 0.421 |
| bench_grid | 2 | low | 0 | 0.264 | 0.273 | 0.967 | 0.789 | 0.547 | 1.442 | 1.04 | 0.695 | 1.496 | 0.000 | 0.000 |
| bench_grid | 2 | medium | 0 | 0.353 | 0.359 | 0.982 | 0.931 | 0.631 | 1.476 | 1.15 | 0.727 | 1.575 | 0.000 | 0.000 |
| bench_grid | 2 | high | 0 | 0.411 | 0.421 | 0.976 | 1.05 | 0.672 | 1.569 | 2.44 | 0.884 | 2.759 | 0.000 | 0.000 |
| bench_grid | 4 | low | 0 | 0.777 | 0.620 | 1.253 | 3.42 | 1.35 | 2.527 | 7.16 | 2.20 | 3.248 | 0.348 | 0.095 |
| bench_grid | 4 | medium | 0 | 1.34 | 0.853 | 1.573 | 4.07 | 1.54 | 2.652 | 8.53 | 2.98 | 2.857 | 0.622 | 0.129 |
| bench_grid | 4 | high | 0 | 1.90 | 1.12 | 1.694 | 8.63 | 2.74 | 3.154 | 11.9 | 3.96 | 3.009 | 0.902 | 0.191 |
| bench_grid | 8 | low | 0 | 4.76 | 1.93 | 2.472 | 18.5 | 5.62 | 3.298 | 25.9 | 7.11 | 3.641 | 3.43 | 0.630 |
| bench_grid | 8 | medium | 0 | 7.63 | 2.69 | 2.841 | 24.3 | 6.29 | 3.864 | 35.6 | 9.39 | 3.789 | 6.01 | 1.06 |
| bench_grid | 8 | high | 0 | 14.7 | 4.26 | 3.439 | 44.3 | 11.2 | 3.957 | 66.9 | 18.2 | 3.675 | 12.5 | 2.21 |
| bench_grid | 16 | low | 0 | 43.9 | 11.8 | 3.723 | 76.6 | 23.2 | 3.302 | 112 | 34.6 | 3.229 | 39.5 | 7.87 |
| bench_grid | 16 | medium | 0 | 66.6 | 16.3 | 4.077 | 132 | 31.6 | 4.172 | 151 | 38.4 | 3.939 | 60.8 | 11.2 |
| bench_grid | 16 | high | 0 | 135 | 30.7 | 4.396 | 235 | 60.4 | 3.89 | 264 | 72.1 | 3.659 | 126 | 23.7 |
| bench_grid | 32 | low | 0 | 341 | 105 | 3.262 | 547 | 174 | 3.154 | 617 | 203 | 3.036 | 319 | 85.6 |
| bench_grid | 32 | medium | 0 | 556 | 161 | 3.452 | 789 | 252 | 3.132 | 896 | 296 | 3.026 | 527 | 137 |
| bench_grid | 32 | high | 0 | 1048 | 318 | 3.297 | 1417 | 449 | 3.155 | 1602 | 514 | 3.117 | 1003 | 280 |
| bench_grid | 8 | medium | 4 | 9.79 | 3.46 | 2.829 | 32.1 | 8.17 | 3.927 | 47.6 | 12.4 | 3.829 | 7.78 | 1.42 |
| bench_grid | 16 | medium | 4 | 87.1 | 20.5 | 4.257 | 166 | 37.8 | 4.394 | 197 | 47.6 | 4.141 | 80.7 | 14.5 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| narrow_aisle/2/low/0 | old | 1 | 1.1 | 1.8 | 1.8 |
| narrow_aisle/2/low/0 | new | 2 | 1.1 | 1.8 | 1.8 |
| narrow_aisle/2/medium/0 | new | 1 | 1.1 | 1.8 | 1.8 |
| narrow_aisle/2/medium/0 | old | 2 | 1.1 | 1.8 | 1.82 |
| narrow_aisle/2/high/0 | old | 1 | 1.3 | 1.82 | 1.82 |
| narrow_aisle/2/high/0 | new | 2 | 1.3 | 1.82 | 1.82 |
| narrow_aisle/4/low/0 | new | 1 | 1.2 | 1.82 | 1.82 |
| narrow_aisle/4/low/0 | old | 2 | 1.3 | 1.82 | 1.83 |
| narrow_aisle/4/medium/0 | old | 1 | 2.0 | 1.83 | 1.83 |
| narrow_aisle/4/medium/0 | new | 2 | 1.6 | 1.83 | 1.83 |
| narrow_aisle/4/high/0 | new | 1 | 2.2 | 1.83 | 1.93 |
| narrow_aisle/4/high/0 | old | 2 | 2.9 | 1.93 | 1.93 |
| samples_world/2/low/0 | old | 1 | 1.0 | 1.93 | 1.93 |
| samples_world/2/low/0 | new | 2 | 1.0 | 1.93 | 2.01 |
| samples_world/2/medium/0 | new | 1 | 1.1 | 2.01 | 2.01 |
| samples_world/2/medium/0 | old | 2 | 1.1 | 2.01 | 2.01 |
| samples_world/2/high/0 | old | 1 | 1.3 | 2.01 | 2.01 |
| samples_world/2/high/0 | new | 2 | 1.3 | 2.01 | 5.05 |
| samples_world/4/low/0 | new | 1 | 1.1 | 5.05 | 5.05 |
| samples_world/4/low/0 | old | 2 | 1.2 | 5.05 | 5.05 |
| samples_world/4/medium/0 | old | 1 | 1.8 | 5.05 | 5.05 |
| samples_world/4/medium/0 | new | 2 | 1.5 | 5.05 | 4.81 |
| samples_world/4/high/0 | new | 1 | 1.8 | 4.81 | 4.81 |
| samples_world/4/high/0 | old | 2 | 2.3 | 4.81 | 5.39 |
| bench_grid/2/low/0 | old | 1 | 1.3 | 5.39 | 5.39 |
| bench_grid/2/low/0 | new | 2 | 1.2 | 5.39 | 5.39 |
| bench_grid/2/medium/0 | new | 1 | 1.3 | 5.39 | 5.39 |
| bench_grid/2/medium/0 | old | 2 | 1.4 | 5.39 | 5.03 |
| bench_grid/2/high/0 | old | 1 | 1.5 | 5.03 | 5.03 |
| bench_grid/2/high/0 | new | 2 | 1.4 | 5.03 | 5.03 |
| bench_grid/4/low/0 | new | 1 | 1.7 | 5.03 | 5.03 |
| bench_grid/4/low/0 | old | 2 | 2.1 | 5.03 | 7.11 |
| bench_grid/4/medium/0 | old | 1 | 2.6 | 7.11 | 7.11 |
| bench_grid/4/medium/0 | new | 2 | 1.9 | 7.11 | 8.15 |
| bench_grid/4/high/0 | new | 1 | 2.3 | 8.15 | 8.15 |
| bench_grid/4/high/0 | old | 2 | 3.7 | 8.15 | 7.65 |
| bench_grid/8/low/0 | old | 1 | 7.5 | 7.65 | 7.28 |
| bench_grid/8/low/0 | new | 2 | 3.4 | 7.28 | 6.94 |
| bench_grid/8/medium/0 | new | 1 | 4.1 | 6.94 | 6.7 |
| bench_grid/8/medium/0 | old | 2 | 10.5 | 6.7 | 6.12 |
| bench_grid/8/high/0 | old | 1 | 20.3 | 6.12 | 4.95 |
| bench_grid/8/high/0 | new | 2 | 6.3 | 4.95 | 4.63 |
| bench_grid/16/low/0 | new | 1 | 14.3 | 4.63 | 6.92 |
| bench_grid/16/low/0 | old | 2 | 48.5 | 6.92 | 3.98 |
| bench_grid/16/medium/0 | old | 1 | 73.6 | 3.98 | 2.57 |
| bench_grid/16/medium/0 | new | 2 | 19.0 | 2.57 | 2.43 |
| bench_grid/16/high/0 | new | 1 | 34.4 | 2.43 | 4.11 |
| bench_grid/16/high/0 | old | 2 | 141.6 | 4.11 | 1.95 |
| bench_grid/32/low/0 | old | 1 | 353.2 | 1.95 | 1.81 |
| bench_grid/32/low/0 | new | 2 | 111.1 | 1.81 | 1.88 |
| bench_grid/32/medium/0 | new | 1 | 169.8 | 1.88 | 1.78 |
| bench_grid/32/medium/0 | old | 2 | 570.4 | 1.78 | 1.84 |
| bench_grid/32/high/0 | old | 1 | 1080.0 | 1.84 | 1.83 |
| bench_grid/32/high/0 | new | 2 | 328.6 | 1.83 | 1.87 |
| bench_grid/8/medium/4 | new | 1 | 5.1 | 1.87 | 1.88 |
| bench_grid/8/medium/4 | old | 2 | 13.6 | 1.88 | 1.91 |
| bench_grid/16/medium/4 | old | 1 | 95.8 | 1.91 | 1.98 |
| bench_grid/16/medium/4 | new | 2 | 23.1 | 1.98 | 1.9 |
