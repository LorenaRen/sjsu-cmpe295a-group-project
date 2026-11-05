# Checker comparison, 2026-11-04

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old bb46d18 --new bb46d18 --cell bench_grid/4/medium/0 --cell bench_grid/8/medium/0 --cell bench_grid/16/medium/0 --out code/mrs-ethics/benchmarks/reports/2026-11-04_Mac14-15_aa` |
| Started from | /bin/zsh |
| Old checker | `bb46d185a4718fd248645ccaeff7f404c9bbece8` (bb46d18) |
| New checker | `bb46d185a4718fd248645ccaeff7f404c9bbece8` (bb46d18) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 1.9, 1.98, 2.08 |
| Busiest other processes at the start (% CPU) | claude 10.9; Code Helper (Renderer) 9.9; searchpartyd 9.3; WindowServer 8.5; dasd 4.5 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (600 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench_grid | 4 | medium | 0 | 1.34 | 1.35 | 0.996 | 4.13 | 4.14 | 0.998 | 8.04 | 8.57 | 0.937 | 0.623 | 0.626 |
| bench_grid | 8 | medium | 0 | 7.69 | 7.74 | 0.994 | 24.2 | 25.0 | 0.968 | 35.2 | 35.8 | 0.984 | 6.02 | 6.04 |
| bench_grid | 16 | medium | 0 | 66.3 | 66.7 | 0.995 | 132 | 131 | 1.002 | 148 | 150 | 0.986 | 60.8 | 60.9 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| bench_grid/4/medium/0 | old | 1 | 2.6 | 1.9 | 1.9 |
| bench_grid/4/medium/0 | new | 2 | 2.6 | 1.9 | 1.9 |
| bench_grid/8/medium/0 | new | 1 | 10.6 | 1.9 | 1.92 |
| bench_grid/8/medium/0 | old | 2 | 10.6 | 1.92 | 1.93 |
| bench_grid/16/medium/0 | old | 1 | 73.4 | 1.93 | 1.74 |
| bench_grid/16/medium/0 | new | 2 | 73.4 | 1.74 | 1.8 |
