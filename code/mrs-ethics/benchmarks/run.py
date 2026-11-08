#!/usr/bin/env python3
"""Run the checker benchmarks (README.md). From the repository root, with the plain venv:

    .venv/bin/python code/mrs-ethics/benchmarks/run.py           # the full run, into reports/<date>_<machine>/
    .venv/bin/python code/mrs-ethics/benchmarks/run.py --quick   # a few scenarios per cell: does everything run?

It fixes PYTHONHASHSEED=0 (by starting itself again with it), and stops with exit code 2 if the
scenarios the generator makes are not the ones in manifest.csv. compare.py runs it on two
versions of the checker (--src) to compare them.
"""
import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv=None) -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        os.execve(sys.executable, [sys.executable, str(HERE / "run.py"), *sys.argv[1:]],
                  {**os.environ, "PYTHONHASHSEED": "0"})
    p = argparse.ArgumentParser(description="Run the checker benchmarks.")
    p.add_argument("--quick", action="store_true", help="a few scenarios per cell, to see that everything runs")
    p.add_argument("--only", choices=("perf", "repair"), help="run only the timing or only the repair benchmark")
    p.add_argument("--cell", action="append", default=[],
                   help="run only this cell, map/robots/density/people, e.g. bench_grid/32/high/0 (repeatable)")
    p.add_argument("--src", type=Path,
                   help="the checker source tree to run, a code/mrs-ethics/src directory (default: this checkout's)")
    p.add_argument("--verdicts", action="store_true",
                   help="instead: write every verdict (timing scenarios, every repair round) to verdicts.jsonl")
    p.add_argument("--out", type=Path, help="where to write the results (default: reports/<date>_<machine>/)")
    p.add_argument("--manifest", type=Path, default=HERE / "manifest.csv",
                   help="the manifest to check the scenarios against")
    p.add_argument("--write-manifest", action="store_true",
                   help="write the manifest for the scenarios the generator makes now (only on purpose)")
    a = p.parse_args(argv)
    if a.src is not None:
        os.environ["BENCH_SRC"] = str(a.src.resolve())   # before bench is imported: it reads it
    sys.path.insert(0, str(HERE))
    from bench.compare import parse_cell
    from bench.maps import load_maps
    from bench.runner import FULL, QUICK, ManifestError, export_verdicts, run
    from bench.scenarios import write_manifest

    if a.write_manifest:
        write_manifest(load_maps(), a.manifest)
        print(f"wrote {a.manifest}")
        return 0
    try:
        cells_filter = {parse_cell(c) for c in a.cell} or None
    except ValueError as e:
        p.error(str(e))
    profile = QUICK if a.quick else FULL
    try:
        if a.verdicts:
            done = export_verdicts(profile, a.out, a.manifest, argv=sys.argv, cells_filter=cells_filter)
        else:
            done = run(profile, a.out, a.only, a.manifest, argv=sys.argv, cells_filter=cells_filter)
    except ManifestError as e:
        print("the scenarios differ from the manifest; nothing was measured:", *e.args[0], sep="\n  ")
        return 2
    print(f"done: {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
