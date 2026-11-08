#!/usr/bin/env python3
"""Compare two versions of the checker on the benchmark (README.md). From the repository root,
with the plain venv; on macOS, under caffeinate, since a full comparison runs for an hour or more:

    caffeinate -i .venv/bin/python code/mrs-ethics/benchmarks/compare.py timing --old <commit> --new <commit>
    caffeinate -i .venv/bin/python code/mrs-ethics/benchmarks/compare.py verdicts --old <commit> --new <commit>

--old and --new are git commits (checked out into temporary worktrees) or code/mrs-ethics/src
directories. timing takes turns cell by cell and logs the load; verdicts checks that both give
the same verdicts on every scenario, byte for byte.
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv=None) -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.execve(sys.executable, [sys.executable, str(HERE / "compare.py"), *sys.argv[1:]],
                  {**os.environ, "PYTHONHASHSEED": "0"})
    sys.path.insert(0, str(HERE))
    from bench.compare import compare_timing, compare_verdicts, parse_cell
    from bench.env import environment
    from bench.runner import FULL, QUICK, default_out

    p = argparse.ArgumentParser(description="Compare two versions of the checker on the benchmark.")
    sub = p.add_subparsers(dest="what", required=True)
    for name, text in (("timing", "time both, cell by cell, in turns"), ("verdicts", "compare every verdict")):
        s = sub.add_parser(name, help=text)
        s.add_argument("--old", required=True, help="a git commit, or a code/mrs-ethics/src directory")
        s.add_argument("--new", required=True, help="a git commit, or a code/mrs-ethics/src directory")
        s.add_argument("--quick", action="store_true", help="the quick profile: a few scenarios per cell")
        s.add_argument("--out", type=Path, help="where to write (default: reports/<date>_<machine>_<what>/)")
        if name == "timing":
            s.add_argument("--cell", action="append", default=[], help="only this cell (repeatable)")
    a = p.parse_args(argv)
    profile = QUICK if a.quick else FULL
    out = a.out or default_out(profile, environment([], profile.name),
                               "_compare" if a.what == "timing" else "_equivalence")
    if a.what == "timing":
        done = compare_timing(a.old, a.new, [parse_cell(c) for c in a.cell], out, a.quick) / "compare.md"
    else:
        done = compare_verdicts(a.old, a.new, out, a.quick) / "equivalence.md"
    print(f"done: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
