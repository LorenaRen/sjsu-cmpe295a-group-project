"""Per-run summary of planning cycles and LLM calls, from the response log (Module 4). Pure Python.

  ros2 run mrs_llm_planner summary                       # llm_log.sqlite in the working directory
  ros2 run mrs_llm_planner summary --log path/to/llm_log.sqlite --run exp1_s01 --json

For each run (and model): how many cycles ended approved or in the fallback, and why; how long
they took; how many rounds, turns, skipped turns and plan versions they used; and the LLM calls,
repair calls, failed calls and tokens behind them. The numbers to compare models with: a small
model may agree on fewer plans, but the process should stay bounded and explain every failure.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Optional


def summarize(path, run_id: Optional[str] = None) -> list[dict]:
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    runs = [run_id] if run_id else [r[0] for r in db.execute(
        "SELECT DISTINCT run_id FROM calls UNION SELECT DISTINCT run_id FROM "
        + ("cycles" if "cycles" in tables else "calls") + " ORDER BY 1")]
    out = []
    for run in runs:
        cycles = [dict(r) for r in db.execute("SELECT * FROM cycles WHERE run_id = ?", (run,))] if "cycles" in tables else []
        calls = [dict(r) for r in db.execute("SELECT * FROM calls WHERE run_id = ?", (run,))]
        per_turn = Counter((c["agent"], c["transaction_id"], c["round"]) for c in calls)
        n = len(cycles)
        mean = lambda key: round(sum(c[key] or 0 for c in cycles) / n, 2) if n else None
        out.append({
            "run_id": run,
            "models": sorted({c["model"] for c in cycles if c["model"]} | {c["model"] for c in calls if c["model"]}),
            "cycles": n,
            "approved": sum(c["outcome"] == "approved" for c in cycles),
            "fallback": sum(c["outcome"] == "fallback" for c in cycles),
            "fallback_causes": dict(Counter(c["cause"] for c in cycles if c["outcome"] == "fallback")),
            "mean_duration_s": mean("duration_s"),
            "mean_rounds": mean("rounds"),
            "mean_turns": mean("turns"),
            "skipped_turns": sum(c["skips"] or 0 for c in cycles),
            "mean_plan_versions": mean("plan_versions"),
            "llm_calls": len(calls),
            "repair_calls": sum(k - 1 for k in per_turn.values() if k > 1),
            "failed_calls": sum(c["error"] is not None for c in calls),
            "failed_by_reason": dict(Counter(c["error"].split(":", 1)[0] for c in calls if c["error"])),
            "tokens_in": sum(c["tokens_in"] or 0 for c in calls),
            "tokens_out": sum(c["tokens_out"] or 0 for c in calls),
            "mean_call_s": round(sum(c["latency_ms"] or 0 for c in calls if not c["error"])
                                 / max(1, sum(c["error"] is None for c in calls)) / 1000, 2),
        })
    db.close()
    return out


def _text(s: dict) -> str:
    rate = f"{100 * s['approved'] / s['cycles']:.0f}%" if s["cycles"] else "-"
    causes = ", ".join(f"{k} {v}" for k, v in sorted(s["fallback_causes"].items())) or "none"
    failed = ", ".join(f"{k} {v}" for k, v in sorted(s["failed_by_reason"].items())) or "none"
    return "\n".join([
        f"run {s['run_id']}  ({', '.join(s['models']) or 'no model'})",
        f"  cycles {s['cycles']}: approved {s['approved']} ({rate}), fallback {s['fallback']} [{causes}]",
        f"  per cycle: {s['mean_duration_s']} s, {s['mean_rounds']} rounds, {s['mean_turns']} turns, "
        f"{s['mean_plan_versions']} plan versions; skipped turns {s['skipped_turns']} in total",
        f"  LLM: {s['llm_calls']} calls ({s['repair_calls']} repairs, {s['failed_calls']} failed [{failed}]), "
        f"{s['mean_call_s']} s per call, tokens {s['tokens_in']} in / {s['tokens_out']} out",
    ])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Summarize planning cycles and LLM calls of a response log.")
    p.add_argument("--log", default="llm_log.sqlite", help="the response log (default: ./llm_log.sqlite)")
    p.add_argument("--run", default=None, help="one run_id (default: every run in the log)")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    a = p.parse_args(argv)
    if not Path(a.log).is_file():
        print(f"no such log: {a.log}", file=sys.stderr)
        return 1
    result = summarize(a.log, a.run)
    print(json.dumps(result, indent=2) if a.json else "\n\n".join(_text(s) for s in result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
