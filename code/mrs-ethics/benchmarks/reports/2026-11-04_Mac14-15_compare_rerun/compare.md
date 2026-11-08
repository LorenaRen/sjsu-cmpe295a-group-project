# Checker comparison, 2026-11-04

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old bb46d18 --new 120a928 --cell samples_world/2/high/0 --cell samples_world/4/low/0 --cell samples_world/4/medium/0 --cell samples_world/4/high/0 --cell bench_grid/2/low/0 --cell bench_grid/2/medium/0 --cell bench_grid/2/high/0 --cell bench_grid/4/low/0 --cell bench_grid/4/medium/0 --cell bench_grid/4/high/0 --cell bench_grid/8/low/0 --cell bench_grid/8/medium/0 --cell bench_grid/8/high/0 --cell bench_grid/16/low/0 --cell bench_grid/16/medium/0 --cell bench_grid/16/high/0 --out code/mrs-ethics/benchmarks/reports/2026-11-04_Mac14-15_compare_rerun` |
| Started from | /bin/zsh |
| Old checker | `bb46d185a4718fd248645ccaeff7f404c9bbece8` (bb46d18) |
| New checker | `120a928b8e01a567b8d303abeab59c699f5a8515` (120a928) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 2.12, 2.33, 2.18 |
| Busiest other processes at the start (% CPU) | Code Helper (Renderer) 11; WindowServer 10.6; claude 10.2; bluetoothd 2.8; Code Helper 2.1 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (3200 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| samples_world | 2 | high | 0 | 0.340 | 0.287 | 1.182 | 0.559 | 0.445 | 1.257 | 0.578 | 0.486 | 1.188 | 0.148 | 0.093 |
| samples_world | 4 | low | 0 | 0.126 | 0.138 | 0.911 | 0.396 | 0.345 | 1.146 | 0.622 | 0.492 | 1.264 | 0.000 | 0.000 |
| samples_world | 4 | medium | 0 | 0.729 | 0.541 | 1.347 | 1.87 | 1.17 | 1.595 | 2.24 | 1.29 | 1.739 | 0.411 | 0.218 |
| samples_world | 4 | high | 0 | 1.22 | 0.809 | 1.503 | 1.93 | 1.23 | 1.569 | 2.14 | 1.29 | 1.654 | 0.824 | 0.418 |
| bench_grid | 2 | low | 0 | 0.265 | 0.274 | 0.969 | 0.793 | 0.556 | 1.427 | 1.10 | 0.700 | 1.571 | 0.000 | 0.000 |
| bench_grid | 2 | medium | 0 | 0.352 | 0.359 | 0.979 | 0.937 | 0.628 | 1.491 | 1.12 | 0.729 | 1.54 | 0.000 | 0.000 |
| bench_grid | 2 | high | 0 | 0.410 | 0.423 | 0.97 | 1.00 | 0.674 | 1.487 | 2.40 | 0.846 | 2.842 | 0.000 | 0.000 |
| bench_grid | 4 | low | 0 | 0.765 | 0.620 | 1.234 | 3.31 | 1.34 | 2.463 | 7.12 | 2.21 | 3.221 | 0.346 | 0.095 |
| bench_grid | 4 | medium | 0 | 1.33 | 0.849 | 1.568 | 4.06 | 1.53 | 2.652 | 8.18 | 3.02 | 2.708 | 0.623 | 0.127 |
| bench_grid | 4 | high | 0 | 1.88 | 1.13 | 1.668 | 8.58 | 2.84 | 3.019 | 11.8 | 4.02 | 2.939 | 0.898 | 0.192 |
| bench_grid | 8 | low | 0 | 4.74 | 1.91 | 2.485 | 18.5 | 5.59 | 3.305 | 25.9 | 7.09 | 3.655 | 3.39 | 0.621 |
| bench_grid | 8 | medium | 0 | 7.64 | 2.67 | 2.861 | 24.9 | 6.25 | 3.984 | 33.9 | 9.37 | 3.621 | 6.01 | 1.06 |
| bench_grid | 8 | high | 0 | 14.5 | 4.25 | 3.411 | 43.7 | 11.1 | 3.923 | 66.8 | 18.3 | 3.647 | 12.3 | 2.21 |
| bench_grid | 16 | low | 0 | 43.6 | 11.8 | 3.703 | 79.5 | 21.9 | 3.635 | 112 | 31.0 | 3.619 | 39.3 | 7.86 |
| bench_grid | 16 | medium | 0 | 66.2 | 16.2 | 4.081 | 131 | 31.6 | 4.135 | 148 | 38.4 | 3.851 | 60.7 | 11.2 |
| bench_grid | 16 | high | 0 | 135 | 30.5 | 4.428 | 233 | 60.2 | 3.874 | 263 | 72.1 | 3.643 | 126 | 23.5 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| samples_world/2/high/0 | old | 1 | 1.4 | 2.12 | 2.12 |
| samples_world/2/high/0 | new | 2 | 1.3 | 2.12 | 2.12 |
| samples_world/4/low/0 | new | 1 | 1.1 | 2.12 | 2.19 |
| samples_world/4/low/0 | old | 2 | 1.2 | 2.19 | 2.19 |
| samples_world/4/medium/0 | old | 1 | 1.8 | 2.19 | 2.19 |
| samples_world/4/medium/0 | new | 2 | 1.5 | 2.19 | 2.19 |
| samples_world/4/high/0 | new | 1 | 1.8 | 2.19 | 2.49 |
| samples_world/4/high/0 | old | 2 | 2.3 | 2.49 | 2.49 |
| bench_grid/2/low/0 | old | 1 | 1.3 | 2.49 | 2.61 |
| bench_grid/2/low/0 | new | 2 | 1.2 | 2.61 | 2.61 |
| bench_grid/2/medium/0 | new | 1 | 1.3 | 2.61 | 2.61 |
| bench_grid/2/medium/0 | old | 2 | 1.4 | 2.61 | 2.61 |
| bench_grid/2/high/0 | old | 1 | 1.4 | 2.61 | 2.65 |
| bench_grid/2/high/0 | new | 2 | 1.4 | 2.65 | 2.65 |
| bench_grid/4/low/0 | new | 1 | 1.7 | 2.65 | 2.65 |
| bench_grid/4/low/0 | old | 2 | 2.1 | 2.65 | 3.23 |
| bench_grid/4/medium/0 | old | 1 | 2.6 | 3.23 | 3.23 |
| bench_grid/4/medium/0 | new | 2 | 1.9 | 3.23 | 3.05 |
| bench_grid/4/high/0 | new | 1 | 2.3 | 3.05 | 3.05 |
| bench_grid/4/high/0 | old | 2 | 3.7 | 3.05 | 2.97 |
| bench_grid/8/low/0 | old | 1 | 7.5 | 2.97 | 2.89 |
| bench_grid/8/low/0 | new | 2 | 3.4 | 2.89 | 2.9 |
| bench_grid/8/medium/0 | new | 1 | 4.1 | 2.9 | 2.83 |
| bench_grid/8/medium/0 | old | 2 | 10.5 | 2.83 | 3.02 |
| bench_grid/8/high/0 | old | 1 | 20.2 | 3.02 | 2.67 |
| bench_grid/8/high/0 | new | 2 | 6.3 | 2.67 | 2.53 |
| bench_grid/16/low/0 | new | 1 | 14.2 | 2.53 | 2.27 |
| bench_grid/16/low/0 | old | 2 | 48.5 | 2.27 | 2.06 |
| bench_grid/16/medium/0 | old | 1 | 73.1 | 2.06 | 2.11 |
| bench_grid/16/medium/0 | new | 2 | 19.0 | 2.11 | 2.31 |
| bench_grid/16/high/0 | new | 1 | 34.3 | 2.31 | 2.12 |
| bench_grid/16/high/0 | old | 2 | 141.2 | 2.12 | 1.98 |
