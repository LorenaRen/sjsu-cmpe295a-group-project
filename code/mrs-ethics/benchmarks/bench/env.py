"""What a run measured on: the machine, the Python and the code (macOS tools where there are any)."""
from __future__ import annotations

import os
import platform
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import pydantic

from . import SRC, config


def _run(*cmd: str) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _int(text: str | None) -> int | None:
    return int(text) if text and text.isdigit() else None


def _power() -> str | None:
    """macOS: where the power comes from, e.g. 'AC Power' or 'Battery Power'."""
    out = _run("pmset", "-g", "batt")
    if not out:
        return None
    first = out.splitlines()[0]
    return first.split("'")[1] if first.count("'") >= 2 else first


def _busiest(count: int = 5) -> list[dict[str, Any]]:
    """The processes using the most CPU when the run starts, this one left out."""
    rows = []
    for line in (_run("ps", "-A", "-o", "pid=,%cpu=,comm=") or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) != os.getpid():
            rows.append({"pid": int(parts[0]), "cpu_percent": float(parts[1]), "command": Path(parts[2]).name})
    return sorted(rows, key=lambda r: -r["cpu_percent"])[:count]


def environment(argv: Sequence[str], profile: str) -> dict[str, Any]:
    memory = _int(_run("sysctl", "-n", "hw.memsize"))
    git = ("git", "-C", str(config.BENCH_DIR))
    return {
        "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "command": shlex.join([sys.executable, *argv]),
        "profile": profile,
        "git_commit": _run(*git, "rev-parse", "HEAD"),
        "git_dirty": bool(_run(*git, "status", "--porcelain", "--untracked-files=no")),
        "machine": {
            "model": _run("sysctl", "-n", "hw.model"),
            "cpu": _run("sysctl", "-n", "machdep.cpu.brand_string") or platform.processor(),
            "performance_cores": _int(_run("sysctl", "-n", "hw.perflevel0.physicalcpu")),
            "efficiency_cores": _int(_run("sysctl", "-n", "hw.perflevel1.physicalcpu")),
            "logical_cpus": os.cpu_count(),
            "memory_gb": round(memory / 2 ** 30, 1) if memory else None,
            "os": platform.platform(),
        },
        "checker_src": {"path": str(SRC), "git_commit": _run("git", "-C", str(SRC), "rev-parse", "HEAD"),
                        "git_dirty": bool(_run("git", "-C", str(SRC), "status", "--porcelain", "--untracked-files=no"))},
        "parent_process": _run("ps", "-o", "comm=", "-p", str(os.getppid())),
        "power": _power(),
        "load_average": [round(x, 2) for x in os.getloadavg()],
        "busiest_processes": _busiest(),
        "python": {"version": platform.python_version(), "implementation": platform.python_implementation(),
                   "executable": sys.executable, "pydantic": pydantic.VERSION,
                   "hash_seed": os.environ.get("PYTHONHASHSEED")},
    }
