# Why 32 robots gained less than 16 from incremental rechecks

Incremental rechecks (PR #15) gave a median speedup of 3.70 / 4.08 / 4.43 at 16 robots (low / medium / high density on bench_grid), but only 3.26 / 3.45 / 3.30 at 32. Was that the quadratic lookup of R-1's yielders in `team_check._check`?

**Mostly, yes.**

- If neither version had the lookup, 32 robots would gain about as much as 16 at low and medium density. The gap shrinks from −12% to −1% at low density and from −15% to −3% at medium.
- At high density the gap shrinks less, from −25% to −14%.
- What makes up the rest at high density was not measured. It is not that rechecks rebuild more robots at 32: a recheck rebuilds 12%, 6% and 3% of 8, 16 and 32 robots, about one robot at every fleet size (second table).

## How this was worked out

f88f03b finds each conflict's yielders in one pass (the commit before these results).

**Which runs the times come from.** Each cell's times come from the run of its session that measured it at a quiet load:
- 8 and 16 robots: the reruns.
- 32 robots: the first runs.

**Two sessions, same day, same machine.**
- *First session:* bb46d18 (before incremental rechecks) → 120a928 (incremental rechecks). Data in `../2026-11-04_Mac14-15_compare{,_rerun}`.
- *This session:* 969d5a8 (main, incremental rechecks) → f88f03b (incremental rechecks + one-pass lookup). Data in `./` and `../2026-11-04_Mac14-15_r1_compare_rerun`.

**Drift between the sessions.** 969d5a8 runs the same check and verdict code as 120a928. (Main also gained PR #14 in between, but that changes mrs_common's message conversion and one reason code, none of it timed.) So timing 969d5a8 here against 120a928 in the first session measures drift between the sessions: −0.2% to +1.8%.

**The lookup's cost** is L = main − one pass (medians, this session).

**The counterfactual.** Without the lookup in either version, the incremental speedup would be (bb46d18 − L) / one pass.
- This assumes the lookup costs the same in bb46d18 as in main. Both run it once per check and per recheck, on the same conflicts: every scenario's findings and number of rechecks were the same on all sides.
- Medians do not add, so the same quantity was also computed per scenario, from the per-scenario medians in the runs' `runs/` directories (local, not in git). The per-scenario values are 3.83 / 4.31 / 4.79 at 16 robots and 3.78 / 4.05 / 4.03 at 32. The gap at 32 is then −1% / −6% / −16%, the same picture.

All numbers below come from `answer.py`, which reads the committed `compare.csv` files and counts the work by running the checker on the same scenarios:

```bash
.venv/bin/python code/mrs-ethics/benchmarks/reports/2026-11-04_Mac14-15_r1_compare/answer.py
```

## Times

Medians of 200 scenarios × 5 repetitions, in ms. Column key:
- **speedup**: the one-pass lookup's speedup over main.
- **share**: the lookup's share of main's time.
- **drift**: main this session vs 120a928 in the first session.
- **measured**: the incremental speedup measured in the first session.
- **without lookup**: the incremental speedup if neither version had the lookup.
- **total**: bb46d18 → one pass.

| robots, density | main | one pass | speedup | share | drift | measured | without lookup | total |
|---|---|---|---|---|---|---|---|---|
| 8 low | 1.943 | 1.925 | 1.010 | 1% | +1.8% | 2.49 | 2.45 | 2.46 |
| 8 medium | 2.679 | 2.668 | 1.004 | 0% | +0.4% | 2.86 | 2.86 | 2.86 |
| 8 high | 4.292 | 4.248 | 1.010 | 1% | +1.1% | 3.41 | 3.40 | 3.41 |
| 16 low | 11.87 | 11.29 | 1.051 | 5% | +0.8% | 3.70 | 3.81 | 3.86 |
| 16 medium | 16.47 | 15.55 | 1.059 | 6% | +1.4% | 4.08 | 4.20 | 4.26 |
| 16 high | 31.02 | 27.99 | 1.108 | 10% | +1.8% | 4.43 | 4.71 | 4.82 |
| 32 low | 104.7 | 85.41 | 1.226 | 18% | +0.1% | 3.26 | 3.77 | 4.00 |
| 32 medium | 161.5 | 127.9 | 1.263 | 21% | +0.3% | 3.45 | 4.08 | 4.35 |
| 32 high | 317.3 | 241.4 | 1.314 | 24% | −0.2% | 3.30 | 4.03 | 4.34 |

- **The lookup's share of main's time grows with the fleet:** 0–1% at 8 robots, 5–10% at 16, 18–24% at 32.
- **Why that lowers the speedup at 32.** bb46d18 pays the same lookup, but it is a much smaller share of bb46d18's time, because bb46d18's rechecks compile and check the whole plan. Since both versions add the same L, their ratio is pulled towards 1 as L grows.
- **The "total" column crosses sessions:** bb46d18 from the first session, f88f03b from this one. Its error is about the drift above.

## Work in the lookup

Medians over the 200 scenarios of each cell. "Old comparisons" means the old lookup's comparisons per scenario; "entries read" means the one-pass lookup's entries read per scenario.

| robots, density | checks (1 + rechecks) | conflicts per check | old comparisons | entries read | robots a recheck rebuilds (mean share) |
|---|---|---|---|---|---|
| 8 low | 4 | 2.5 | 33 | 11 | 12% |
| 8 medium | 6 | 3 | 80 | 21 | 12% |
| 8 high | 9 | 5 | 320 | 54 | 12% |
| 16 low | 21 | 16 | 5,217 | 346 | 6% |
| 16 medium | 25 | 19 | 9,670 | 520 | 6% |
| 16 high | 38 | 28.5 | 32,460 | 1,244 | 6% |
| 32 low | 71 | 53 | 245,604 | 4,801 | 3% |
| 32 medium | 90 | 69 | 505,472 | 7,690 | 3% |
| 32 high | 132 | 94 | 1,535,631 | 16,682 | 3% |

- **The old lookup grows with the square of the conflicts.** From 16 to 32 robots, a scenario's checks grow 3.4–3.6× and each check's conflicts 3.3–3.6×, but the old lookup's comparisons grow 47–52×.
- **The one-pass lookup reads each listed entry once.**
- **A recheck rebuilds about one robot at every fleet size** (12%, 6% and 3% of 8, 16 and 32 robots). So rebuilding is not what grows at 32.
- **What does grow is the per-conflict work every recheck still does for the whole fleet,** because the number of conflicts per check grows faster than the number of robots. That part of the work and its cost were not measured one by one.

## Conditions

| | |
|---|---|
| Machine | Mac14,15, Apple M2 (4 performance + 4 efficiency cores), 8 GB, macOS 26.5.2; CPython 3.14.4, pydantic 2.14.1, `PYTHONHASHSEED=0` |
| Power | AC; every run under `caffeinate -i` ("Kept awake by" in each `compare.md`); Chrome not running |
| This session | A/B 22:05–22:26: load 1.80–6.68 before and after each process; the 32-robot processes 1.80–3.80 (3.80 at the start of the first, falling). Rerun of 8 and 16 robots 22:27–22:30: load 1.70–2.38 |
| Same work | every scenario's counts identical on both sides (1800 scenarios in the A/B, 1200 in the rerun); all 9439 verdicts identical byte for byte (`../2026-11-04_Mac14-15_r1_equivalence`) |

**Why the load rose during the A/B.**
- The load went up to 3–6.7 during the 8- and 16-robot cells. The likely cause is iCloud.
  - The repository is on the Desktop, which syncs to iCloud ("Desktop & Documents Folders" is on).
  - The A/B started right after the equivalence run had written 66 MB of verdict files into the repository, and fileproviderd was at 30.8% CPU at the start.
- The rerun wrote its output outside the synced folder (copied here afterwards), and its load stayed at 1.70–2.38.
- With the rerun's numbers, the 8- and 16-robot cells' median speedups are within −3.6% to +0.7% of the A/B's.
