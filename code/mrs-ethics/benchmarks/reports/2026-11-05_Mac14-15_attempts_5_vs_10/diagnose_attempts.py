"""Repairability with 5 attempts and with 10, on the same 1350 scenarios: of the runs that use up
5 attempts, how many only needed more attempts? Measurement only: the checker and the fake
planner are not changed.

    PYTHONHASHSEED=0 .venv/bin/python <this file> run <out dir>   # the two runs and the detail pass
    .venv/bin/python <this file> report <dir>                    # diagnosis.md from what is in <dir>

`run` calls the benchmark's own repair run (bench.runner.run, as `run.py --only repair`) twice on
this checkout's checker, with config.REPAIR_MAX_ATTEMPTS set to 5 and then 10 (run.py has no option
for it), into <out>/5_attempts and <out>/10_attempts. The fake planner's first 5 rounds do not
depend on the limit, so a run that ends within 5 rounds ends the same way with 10 (`report`
checks it), and a run out of attempts at 5 shows what 5 more attempts do. Then it runs each
scenario still out of attempts at 10 once more, the same way, and records how many constraints
each round's verdict had (still_out_of_attempts.csv).
"""
from __future__ import annotations

import csv
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

BENCH = Path(os.environ.get("BENCH_DIR") or Path(__file__).resolve().parents[2])   # reports/<dir>/ -> benchmarks/
OUTCOMES = ("first_round", "kept_targets", "changed_targets", "stuck", "exhausted")
LABELS = {"first_round": "approved in round 1", "kept_targets": "repaired, targets kept",
          "changed_targets": "repaired, a target moved", "stuck": "stuck", "exhausted": "out of attempts"}


def run(out: Path) -> None:
    if os.environ.get("PYTHONHASHSEED") != "0":
        sys.exit("set PYTHONHASHSEED=0, as run.py does")
    sys.path.insert(0, str(BENCH))
    from bench import config
    from bench.maps import load_maps
    from bench.repair import run_scenario
    from bench.runner import FULL
    from bench.runner import run as bench_run
    from bench.scenarios import generate

    for n in (5, 10):
        config.REPAIR_MAX_ATTEMPTS = n
        bench_run(FULL, out=out / f"{n}_attempts", only="repair", argv=sys.argv)
    maps = load_maps()
    with open(out / "still_out_of_attempts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["scenario_id"] + [f"round_{i}_constraints" for i in range(1, 11)])
        for r in scenarios(out / "10_attempts"):
            if r["outcome"] != "exhausted":
                continue
            map_name, robots, density, people, index = r["scenario_id"].split("/")
            counts: list[int] = []
            run_scenario(generate(maps, map_name, int(robots), density, int(index), int(people)), maps[map_name], 10,
                         on_verdict=lambda attempt, v: counts.append(len(v.constraints)))
            w.writerow([r["scenario_id"]] + counts)


def scenarios(run_dir: Path) -> list[dict[str, str]]:
    with open(run_dir / "repair_scenarios.csv", newline="") as f:
        return list(csv.DictReader(f))


def table(header: list[str], body: list[list[str]]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(r) + " |" for r in body]


def share(k: int, n: int) -> str:
    return f"{k / n:.1%} ({k})" if n else "-"


def report(d: Path) -> None:
    five = {r["scenario_id"]: r for r in scenarios(d / "5_attempts")}
    ten = {r["scenario_id"]: r for r in scenarios(d / "10_attempts")}
    assert five.keys() == ten.keys()
    n = len(five)
    out = ["# Out of attempts: not enough attempts, or no way through?", "",
           f"The repair benchmark's {n} scenarios, with the fake planner allowed 5 attempts and 10, on checker "
           f"`{git_commit(d / '10_attempts')}`. Written by `diagnose_attempts.py` from the runs beside it.", ""]

    # 1. the same up to round 5
    ended = [s for s in five if five[s]["outcome"] != "exhausted"]
    differ = [s for s in ended if (five[s]["outcome"], five[s]["rounds"]) != (ten[s]["outcome"], ten[s]["rounds"])]
    out += ["## Check: rounds 1–5 do not depend on the limit", "",
            f"Of the {len(ended)} runs that ended within 5 rounds, {len(differ)} ended differently with 10 attempts.", ""]

    # 2. overall
    c5, c10 = Counter(r["outcome"] for r in five.values()), Counter(r["outcome"] for r in ten.values())
    out += ["## All scenarios", ""]
    out += table(["outcome", "5 attempts", "10 attempts"],
                 [[LABELS[o], share(c5[o], n), share(c10[o], n)] for o in OUTCOMES]) + [""]

    # 3. what 5 more attempts did to the runs out of attempts at 5
    out5 = [s for s in five if five[s]["outcome"] == "exhausted"]
    then = Counter(ten[s]["outcome"] for s in out5)
    approved = [s for s in out5 if ten[s]["outcome"] in ("kept_targets", "changed_targets")]
    by_round = Counter(int(ten[s]["passed_in_round"]) for s in approved)
    stuck_round = Counter(int(ten[s]["rounds"]) for s in out5 if ten[s]["outcome"] == "stuck")
    out += [f"## The {len(out5)} runs out of attempts at 5, with 10 attempts", ""]
    out += table(["with 10 attempts", "runs", "share"],
                 [[LABELS[o], str(then[o]), f"{then[o] / len(out5):.1%}"] for o in OUTCOMES if o != "first_round"])
    out += ["", "Approved in round: " + ", ".join(f"{k}: {by_round[k]}" for k in sorted(by_round)) + ".",
            "Stuck in round: " + (", ".join(f"{k}: {stuck_round[k]}" for k in sorted(stuck_round)) or "none") + ".", ""]

    # 4. by map and fleet size
    out += ["## By map and fleet size", "",
            "Runs out of attempts at 5, and what they became with 10 attempts.", ""]
    groups: dict[tuple[str, int], list[str]] = {}
    for s in out5:
        m, robots = s.split("/")[:2]
        groups.setdefault((m, int(robots)), []).append(s)
    body = []
    for (m, robots), ss in sorted(groups.items()):
        t = Counter(ten[s]["outcome"] for s in ss)
        body.append([m, str(robots), str(len(ss)), str(t["kept_targets"] + t["changed_targets"]), str(t["stuck"]),
                     str(t["exhausted"])])
    out += table(["map", "robots", "out of attempts at 5", "approved by 10", "stuck by 10", "still out at 10"], body)

    # 5. the runs still out of attempts at 10: heading for a pass?
    with open(d / "still_out_of_attempts.csv", newline="") as f:
        still = list(csv.DictReader(f))
    if still:
        r5 = [int(r["round_5_constraints"]) for r in still]
        r10 = [int(r["round_10_constraints"]) for r in still]
        fewer = sum(b < a for a, b in zip(r5, r10))
        same = sum(b == a for a, b in zip(r5, r10))
        out += ["", f"## The {len(still)} runs still out of attempts at 10", "",
                "Constraints in each round's verdict (a second, identical pass over these scenarios).", ""]
        out += table(["", "round 1", "round 5", "round 10"],
                     [["median", *(f"{statistics.median(int(r[f'round_{i}_constraints']) for r in still):g}"
                                   for i in (1, 5, 10))],
                      ["mean", *(f"{statistics.mean(int(r[f'round_{i}_constraints']) for r in still):.1f}"
                                 for i in (1, 5, 10))]])
        out += ["", f"Round 10 against round 5: fewer constraints in {fewer}, as many in {same}, more in "
                    f"{len(still) - fewer - same}.", ""]

    # 6. the answer
    out += ["## Answer", "",
            f"- **Not enough attempts:** {len(approved)} of the {len(out5)} runs out of attempts at 5 "
            f"({len(approved) / len(out5):.1%}) were approved within 10 attempts.",
            f"- **Blocked:** {then['stuck']} ({then['stuck'] / len(out5):.1%}) got stuck by round 10: a REJECT with "
            "nothing the fake planner could apply.",
            f"- **Not settled by 10 attempts:** {then['exhausted']} ({then['exhausted'] / len(out5):.1%}) were still "
            "rejected after 10. Whether more attempts would do is not measured; the fake planner never proposes "
            "the same plan twice, so these are not loops, but nothing here shows they would converge.", ""]
    (d / "diagnosis.md").write_text("\n".join(out))
    print("\n".join(out))


def git_commit(run_dir: Path) -> str:
    import json
    env = json.loads((run_dir / "environment.json").read_text())
    return (env.get("checker_src") or {}).get("git_commit", "?")[:7]


if __name__ == "__main__":
    mode, where = sys.argv[1], Path(sys.argv[2])
    run(where) if mode == "run" else report(where)
