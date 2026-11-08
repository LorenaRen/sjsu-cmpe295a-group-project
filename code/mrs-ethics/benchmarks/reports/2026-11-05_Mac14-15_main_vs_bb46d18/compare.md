# Checker comparison, 2026-11-05

|  |  |
|---|---|
| Command | `/Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/.venv/bin/python /Users/jeffreyai/Desktop/sjsu-cmpe295a-group-project/code/mrs-ethics/benchmarks/compare.py timing --old bb46d18 --new c93795c --cell bench_grid/16/low/0 --cell bench_grid/16/medium/0 --cell bench_grid/16/high/0 --cell bench_grid/32/low/0 --cell bench_grid/32/medium/0 --cell bench_grid/32/high/0 --out /private/tmp/claude-501/-Users-jeffreyai-Desktop-sjsu-cmpe295a-group-project/55f8f882-f3f5-4322-90e4-81f6bc15a6f1/scratchpad/resume_compare` |
| Kept awake by | caffeinate (pid 93504): PreventUserIdleSystemSleep for pid 93503 |
| Old checker | `bb46d185a4718fd248645ccaeff7f404c9bbece8` (bb46d18) |
| New checker | `c93795c7af5c406aba587617a5f2a85c016a827b` (c93795c) |
| Machine | Mac14,15, Apple M2, 4 performance + 4 efficiency cores, 8.0 GB memory, macOS-26.5.2-arm64-arm-64bit-Mach-O |
| Power | AC Power |
| Load average at the start (1, 5, 15 min) | 2.45, 2.49, 2.28 |
| Busiest other processes at the start (% CPU) | Code Helper (Renderer) 60.1; Code Helper (Renderer) 14.4; WindowServer 13.7; accountsd 3.9; Code Helper 3 |
| Python | CPython 3.14.4, pydantic 2.14.1, PYTHONHASHSEED=0 |
| Profile | full |

Each cell ran as one `run.py --only perf --cell` process per side, with its own warm-up; the old side went first in even cells and the new in odd ones. The benchmark's code is this checkout's for both sides: only the checker differs. Speedup is the old value over the new.

Both sides computed the same work: every scenario's counts are the same (1200 scenarios: verdict, findings by rule, constraints, rechecks).

| map | robots | density | people | median old | new | speedup | p95 old | new | speedup | p99 old | new | speedup | recheck median old | new |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| bench_grid | 16 | low | 0 | 44.2 | 11.4 | 3.879 | 81.4 | 21.7 | 3.746 | 113 | 32.1 | 3.527 | 39.9 | 7.41 |
| bench_grid | 16 | medium | 0 | 67.2 | 15.5 | 4.345 | 132 | 30.2 | 4.359 | 151 | 36.1 | 4.177 | 61.5 | 10.4 |
| bench_grid | 16 | high | 0 | 135 | 27.5 | 4.915 | 232 | 52.0 | 4.458 | 267 | 62.3 | 4.279 | 127 | 20.7 |
| bench_grid | 32 | low | 0 | 340 | 85.1 | 3.999 | 550 | 137 | 4.006 | 613 | 164 | 3.746 | 318 | 66.3 |
| bench_grid | 32 | medium | 0 | 556 | 128 | 4.35 | 790 | 191 | 4.143 | 897 | 222 | 4.04 | 526 | 104 |
| bench_grid | 32 | high | 0 | 1046 | 239 | 4.373 | 1415 | 332 | 4.26 | 1527 | 372 | 4.099 | 1002 | 205 |

Times in ms. Load average before and after each process (1 min):

| cell | side | order | seconds | load before | load after |
|---|---|---|---|---|---|
| bench_grid/16/low/0 | old | 1 | 49.3 | 2.45 | 1.99 |
| bench_grid/16/low/0 | new | 2 | 13.8 | 1.99 | 1.91 |
| bench_grid/16/medium/0 | new | 1 | 18.0 | 1.91 | 2.08 |
| bench_grid/16/medium/0 | old | 2 | 74.1 | 2.08 | 1.95 |
| bench_grid/16/high/0 | old | 1 | 141.3 | 1.95 | 1.99 |
| bench_grid/16/high/0 | new | 2 | 31.0 | 1.99 | 1.99 |
| bench_grid/32/low/0 | new | 1 | 90.9 | 1.99 | 2.14 |
| bench_grid/32/low/0 | old | 2 | 352.5 | 2.14 | 1.8 |
| bench_grid/32/medium/0 | old | 1 | 570.2 | 1.8 | 1.91 |
| bench_grid/32/medium/0 | new | 2 | 133.6 | 1.91 | 2.8 |
| bench_grid/32/high/0 | new | 1 | 246.9 | 2.8 | 2.65 |
| bench_grid/32/high/0 | old | 2 | 1078.2 | 2.65 | 1.85 |
