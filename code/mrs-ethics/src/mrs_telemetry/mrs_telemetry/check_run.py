"""Check a run folder after an episode (Module 7). Pure Python: reads the bag's metadata.yaml,
not the bag, so it runs anywhere (no ROS needed).

  ros2 run mrs_telemetry check_run results/dev/20261117-193000
  ros2 run mrs_telemetry check_run <run_dir> --json        # machine-readable, for Module 9
  python -m mrs_telemetry.check_run <run_dir>               # same, without ROS

Exit code 0 when nothing is wrong (warnings allowed), 1 when something is, 2 for a folder that
is not a run folder. What it checks:

  run.json        exists and is JSON
  bag             finished cleanly (metadata.yaml written), its files exist, every required topic
                  has messages; topics of modules that do not exist yet may be empty (listed)
  events.jsonl    every line is JSON, telemetry ended cleanly, decisions match the bag's counts
  gz_log          state.tlog exists and is not empty, if the run recorded one
  llm_log.sqlite  readable; how many LLM calls and planning cycles, if the planner wrote one
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from mrs_common import names as N

from . import run_folder as RF
from .events import read_events
from .topics import publisher_of, required_topics

OK, WARN, ERROR = "ok", "warn", "error"

# events.jsonl type -> the bag topic with the same messages
EVENT_TOPIC = {"mission": N.MISSION, "dialogue": N.DIALOGUE, "proposed_plan": N.PROPOSED_PLAN,
               "verdict": N.VERDICT, "validated_plan": N.VALIDATED_PLAN, "task_status": N.TASK_STATUS,
               "episode_event": N.EPISODE_EVENT}


@dataclass
class Check:
    level: str
    part: str
    text: str


@dataclass
class Report:
    run_dir: str
    checks: list[Check] = field(default_factory=list)
    topics: dict[str, int] = field(default_factory=dict)       # bag topic -> message count
    events: dict[str, int] = field(default_factory=dict)       # event type -> count
    duration_s: Optional[float] = None
    bag_mb: Optional[float] = None

    def add(self, level: str, part: str, text: str) -> None:
        self.checks.append(Check(level, part, text))

    @property
    def ok(self) -> bool:
        return not any(c.level == ERROR for c in self.checks)

    def to_dict(self) -> dict:
        return {**asdict(self), "ok": self.ok}


def read_bag_metadata(bag_dir: Path) -> dict:
    data = yaml.safe_load((bag_dir / "metadata.yaml").read_text())
    return data.get("rosbag2_bagfile_information", data) if isinstance(data, dict) else {}


def _check_bag(rep: Report, run_dir: Path, robots: list[str], scan: bool) -> None:
    bag = run_dir / RF.BAG
    if not bag.is_dir():
        rep.add(ERROR, "bag", f"no {RF.BAG}/ folder: the recorder did not run (record.launch.py)")
        return
    if not (bag / "metadata.yaml").is_file():
        rep.add(ERROR, "bag", "no metadata.yaml: the recorder did not stop cleanly (killed, not Ctrl-C). "
                              f"Rebuild it with: ros2 bag reindex {bag}")
        return
    try:
        meta = read_bag_metadata(bag)
    except (OSError, yaml.YAMLError) as e:
        rep.add(ERROR, "bag", f"metadata.yaml unreadable: {e}")
        return
    files = meta.get("relative_file_paths") or [f.get("path") for f in meta.get("files", []) if isinstance(f, dict)]
    missing = [f for f in files if f and not (bag / f).is_file()]
    if missing:
        rep.add(ERROR, "bag", f"files listed in metadata.yaml are missing: {missing}")
    size = sum((bag / f).stat().st_size for f in files if f and (bag / f).is_file())
    rep.bag_mb = round(size / 1e6, 2)
    ns = (meta.get("duration") or {}).get("nanoseconds")
    rep.duration_s = round(ns / 1e9, 2) if ns else None
    if meta.get("storage_identifier") not in (None, "mcap"):
        rep.add(WARN, "bag", f"storage is {meta.get('storage_identifier')}, not mcap")
    for entry in meta.get("topics_with_message_count") or []:
        name = (entry.get("topic_metadata") or {}).get("name")
        if name:
            rep.topics[name] = int(entry.get("message_count", 0))
    if not rep.topics:
        rep.add(ERROR, "bag", "the bag has no topics")
        return
    rep.add(OK, "bag", f"{sum(rep.topics.values())} messages on {len(rep.topics)} topics, "
                       f"{rep.duration_s} s sim time, {rep.bag_mb} MB")

    required = required_topics(robots, scan=scan) if robots else []
    for t in required:
        if rep.topics.get(t, 0) == 0:
            hint = " (was the mission sent?)" if t == N.MISSION else ""
            rep.add(ERROR, "bag", f"{t}: no messages; published by {publisher_of(t)}{hint}")
    empty = sorted(t for t, n in rep.topics.items() if n == 0 and t not in required)
    if empty:
        by = {}
        for t in empty:
            by.setdefault(publisher_of(t), []).append(t)
        for who, ts in sorted(by.items()):
            rep.add(WARN, "bag", f"empty, published by {who}: {', '.join(ts)}")
    absent = sorted(set(required) - set(rep.topics))
    if absent:
        rep.add(ERROR, "bag", f"not recorded at all (not in the topic list?): {', '.join(absent)}")


def _check_events(rep: Report, run_dir: Path) -> None:
    path = run_dir / RF.EVENTS
    if not path.is_file():
        rep.add(ERROR, "events", f"no {RF.EVENTS}: telemetry_node did not run")
        return
    events, problems = read_events(path)
    for p in problems:
        rep.add(ERROR, "events", p)
    for e in events:
        rep.events[e.get("type", "?")] = rep.events.get(e.get("type", "?"), 0) + 1
    if not rep.events.get("telemetry_start"):
        rep.add(WARN, "events", "no telemetry_start line")
    if not rep.events.get("telemetry_end"):
        rep.add(WARN, "events", "no telemetry_end line: telemetry_node did not stop cleanly")
    decisions = {k: v for k, v in rep.events.items() if not k.startswith("telemetry_")}
    rep.add(OK, "events", f"{len(events)} lines: " + (", ".join(f"{k} {v}" for k, v in sorted(decisions.items()))
                                                       or "no decisions"))
    if rep.topics:   # the bag and the events saw the same topics: their counts should agree
        for kind, topic in EVENT_TOPIC.items():
            in_bag, in_events = rep.topics.get(topic), rep.events.get(kind, 0)
            if in_bag is not None and in_bag != in_events:
                rep.add(WARN, "events", f"{kind}: {in_events} events but {in_bag} messages on {topic} in the bag "
                                        "(one of them started late or stopped early)")


def _check_gz_log(rep: Report, run_dir: Path, info: dict) -> None:
    wanted = (info.get("sim") or {}).get("record_gz_log")
    log = run_dir / RF.GZ_LOG
    tlog = log / "state.tlog"
    if tlog.is_file() and tlog.stat().st_size > 0:
        rep.add(OK, "gz_log", f"state.tlog {round(tlog.stat().st_size / 1e6, 2)} MB")
    elif wanted:
        rep.add(ERROR, "gz_log", f"record_gz_log was on, but {tlog} is missing or empty")
    elif log.exists():
        rep.add(WARN, "gz_log", f"{RF.GZ_LOG}/ exists but has no state.tlog")


def _check_llm_log(rep: Report, run_dir: Path, info: dict) -> None:
    path = run_dir / RF.LLM_LOG
    if not path.is_file():
        if info.get("planner"):
            rep.add(WARN, "llm_log", f"the planner ran but there is no {RF.LLM_LOG}")
        return
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            calls = con.execute("SELECT COUNT(*) FROM calls").fetchone()[0] if "calls" in tables else 0
            cycles = con.execute("SELECT COUNT(*) FROM cycles").fetchone()[0] if "cycles" in tables else 0
        finally:
            con.close()
    except sqlite3.Error as e:
        rep.add(ERROR, "llm_log", f"{RF.LLM_LOG} unreadable: {e}")
        return
    rep.add(OK, "llm_log", f"{calls} LLM calls, {cycles} planning cycles")


def check_run(run_dir: str | Path, *, robots: Optional[list[str]] = None, scan: Optional[bool] = None) -> Report:
    run_dir = Path(run_dir)
    rep = Report(str(run_dir))
    info: dict = {}
    try:
        info = RF.read_run_json(run_dir)
    except ValueError as e:
        rep.add(ERROR, "run.json", f"not JSON: {e}")
    if not info and not any(c.part == "run.json" for c in rep.checks):
        rep.add(WARN, "run.json", "missing: nothing records what ran")
    elif info:
        git = info.get("git") or {}
        commit = git.get("commit", "")[:10] or "unknown commit"
        rep.add(OK, "run.json", f"{info.get('experiment')}/{info.get('run_id')}, {commit}"
                                + (", uncommitted changes" if git.get("dirty") else ""))
    rec = info.get("recording") or {}
    if robots is None:
        robots = rec.get("robots") or []
    if scan is None:
        scan = rec.get("scan", True)
    _check_bag(rep, run_dir, list(robots), bool(scan))
    _check_events(rep, run_dir)
    _check_gz_log(rep, run_dir, info)
    _check_llm_log(rep, run_dir, info)
    return rep


def format_report(rep: Report) -> str:
    mark = {OK: "  ok  ", WARN: " warn ", ERROR: "ERROR "}
    lines = [f"run {rep.run_dir}"] + [f"{mark[c.level]} {c.part:9} {c.text}" for c in rep.checks]
    if rep.topics:
        lines.append("bag topics:")
        width = max(len(t) for t in rep.topics)
        lines += [f"  {t:{width}}  {n}" for t, n in sorted(rep.topics.items())]
    lines.append("OK" if rep.ok else "PROBLEMS FOUND")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="check_run", description="Check a Module 7 run folder.")
    p.add_argument("run_dir")
    p.add_argument("--robots", type=int, default=None, help="fleet size; default: from run.json")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    args = p.parse_args(argv)
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():
        print(f"{run_dir} is not a folder", file=sys.stderr)
        return 2
    rep = check_run(run_dir, robots=N.robot_ids(args.robots) if args.robots else None)
    print(json.dumps(rep.to_dict(), indent=2) if args.json else format_report(rep))
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
