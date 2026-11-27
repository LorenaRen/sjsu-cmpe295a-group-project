"""The run folder: one per episode, self-describing (Module 7). Pure Python.

  results/<experiment>/<run_id>/
    run.json          what ran: git commit, world, robots, model, settings, what was recorded
    bag/              MCAP bag (rosbag2)
    gz_log/           Gazebo state log (state.tlog + resources), for visual replay
    llm_log.sqlite    every LLM request and response, and one row per planning cycle (Module 4)
    events.jsonl      one line per decision event (telemetry_node)
    ros_log/          every node's ROS log files (episode.launch.py sets ROS_LOG_DIR)
    check.json        check_run's report and the run's outcome
    results.json      episode metrics, written at the end (Module 11)
    sim/              the robot SDFs and bridge.yaml sim.launch.py generated

run.json is built up by whoever starts a part of the run: each writes its own section with
update_run_json, so the runner (Module 9), episode.launch.py and record.launch.py never overwrite
one another.
"""
from __future__ import annotations

import json
import os
import platform
import re
import secrets
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

RUN_JSON = "run.json"
EVENTS = "events.jsonl"
BAG = "bag"
GZ_LOG = "gz_log"
LLM_LOG = "llm_log.sqlite"
SIM = "sim"
ROS_LOG = "ros_log"
CHECK = "check.json"
SCHEMA = 1

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(text: str) -> str:
    """A folder-safe name: 'S5 keep clear' -> 'S5_keep_clear'."""
    out = _SAFE.sub("_", text.strip()).strip("._")
    if not out:
        raise ValueError(f"{text!r} gives an empty folder name")
    return out


def new_run_id(when: Optional[datetime] = None, *, suffix: bool = True) -> str:
    """'20261126-110535-3f2a': the date and time, and 4 random hex digits, so that runs started in the
    same second (parallel HPC jobs) never share a folder. Module 9 should pass its own run id anyway."""
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(2)}" if suffix else stamp


def run_dir_for(root: str | Path = "results", experiment: str = "dev", run_id: str = "") -> Path:
    """results/<experiment>/<run_id>, the run id a timestamp when not given."""
    return Path(root).expanduser() / safe_name(experiment) / safe_name(run_id or new_run_id())


def git_info(path: str | Path = ".") -> dict[str, Any]:
    """The commit the code ran from, and whether it had uncommitted changes. {} outside a repo.
    --no-optional-locks: never write the index (a status in a running experiment must change nothing)."""
    def git(*args: str) -> str:
        return subprocess.run(["git", "--no-optional-locks", "-C", str(path), *args], capture_output=True,
                              text=True, timeout=10, check=True).stdout
    try:
        commit = git("rev-parse", "HEAD").strip()
    except (OSError, subprocess.SubprocessError):
        return {}
    info: dict[str, Any] = {"commit": commit}
    try:
        info["branch"] = git("rev-parse", "--abbrev-ref", "HEAD").strip()
        # porcelain lines are 'XY path'; X may be a space, so never strip the output as a whole
        changed = [line[3:] for line in git("status", "--porcelain", "--untracked-files=no").splitlines() if line]
        info["dirty"] = bool(changed)
        if changed:
            info["changed_files"] = changed[:50]
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def host_info() -> dict[str, str]:
    return {"host": socket.gethostname(), "platform": platform.platform(), "python": platform.python_version(),
            "ros_distro": os.environ.get("ROS_DISTRO", "")}


def read_run_json(run_dir: str | Path) -> dict:
    path = Path(run_dir) / RUN_JSON
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def update_run_json(run_dir: str | Path, section: str, data: Mapping[str, Any]) -> dict:
    """Set one section of run.json (replacing that section only) and return the whole file.
    The first writer also stores schema, run_id, created and host."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    info = read_run_json(run_dir)
    if not info:
        info = {"schema": SCHEMA, "run_id": run_dir.name, "experiment": run_dir.parent.name,
                "created": now_utc(), **host_info()}
    info[section] = dict(data)
    tmp = run_dir / (RUN_JSON + ".tmp")
    tmp.write_text(json.dumps(info, indent=2, sort_keys=False, default=str) + "\n")
    tmp.replace(run_dir / RUN_JSON)   # never a half-written run.json
    return info
