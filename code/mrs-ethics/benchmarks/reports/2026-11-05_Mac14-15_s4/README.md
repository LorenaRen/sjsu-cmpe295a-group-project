# S-4's custom constraints, before and after (haoran/s4-actionable)

**The question.** Did making S-4's constraints actionable help the repair loop? Before this
branch, S-4 sent 22,073 `custom` constraints across the repair benchmark's 1350 scenarios, and
43.5% of the runs got stuck on them.

**The answer.** Mostly yes, though many runs now hit the attempt limit instead:

- Stuck runs fell from 43.5% to 3.4%.
- S-4's `custom` constraints fell from 22,073 to 4,708.
- Plans repaired after round 1 rose from 239 to 392.
- Runs out of attempts rose from 10.8% to 39.6%. With actionable constraints the fake planner
  keeps going, but its waiting points are the nearest allowed nodes. These often cause new
  conflicts, and 5 attempts are not enough at 16 and 32 robots on bench_grid, or at 4 robots
  in narrow_aisle and samples_world.

This measures the scripted fake planner with the gate's constraints, not an LLM planner.

## What is here

| Directory or file | What it is |
|---|---|
| `compare.md` | the three repair runs side by side, written by `compare_repair.py` from their CSV files |
| `1_baseline/` | `run.py --only repair` on d64457f: main's checker (c93795c), plus `VerdictContent.cases` to count S-4's constraints by situation |
| `2_fake_planner/` | the same on 14e0480. The fake planner now has a robot that waits where it starts wait elsewhere, and never moves a task to where another robot ends |
| `3_checker/` | the same on 65acde1. The gate now sends `forbid_node` instead of `custom` for three situations: a yielder waiting where it starts, a keeper waiting in the yielder's way, and a keeper staying where the yielder's task ends |
| `step1_equivalence/` | c93795c vs d64457f: all 9439 verdicts identical byte for byte (the labels change no message) |
| `verdicts_vs_main/` | c93795c vs 65acde1: the first 5804 lines identical, including all 5800 verdicts of the timing scenarios (their plans have no `depends_on`, so the new branches never apply); repair rounds differ by design |
| `timing_vs_main/`, `timing_vs_main_rerun/` | c93795c vs 65acde1 timing, bench_grid medium; every scenario's counts identical |

## Timing

Median speedup of 65acde1 over c93795c (1 = no change). The machine was quiet for the rerun.

| bench_grid, medium | first run | rerun |
|---|---|---|
| 8 robots | 1.002 | — |
| 16 robots | 0.985 | 0.998 |
| 32 robots | 0.987 | 0.989 |

- At 32 robots the new checker is about 1% slower in both runs. An earlier A/A run on this machine showed 0.4–0.6% noise.
- Where the time goes was not measured. A candidate: for every S-4 pair, `team_check` now asks `keeper_in_the_way`, and when the keeper stands at the node this builds the task graph again.

## Conditions

- MacBook Mac14,15 (Apple M2), CPython 3.14.4, pydantic 2.14.1, `PYTHONHASHSEED=0`.
- AC power; every run under `caffeinate -i`; Chrome not running.
- Output written outside the iCloud-synced Desktop, then copied here.
- Each run's `environment.json` has the details.
- Repair: 50 scenarios per cell, 27 cells, at most 5 attempts.
