"""Reproducible benchmarks of the shared checker (run.py, compare.py): how long a check takes, and
how far the gate's constraints get a scripted planner. Pure Python: the checker and the gate are
imported from a source tree, with no ROS.

Importing this package puts the source directories on sys.path, as the packages' own pytest.ini
files do: this repository's code/mrs-ethics/src, or the src directory that the environment
variable BENCH_SRC names (run.py --src), so that the same benchmark can run another version of
the checker."""
import os
import sys
from pathlib import Path

SRC = Path(os.environ.get("BENCH_SRC") or Path(__file__).resolve().parents[2] / "src").resolve()
for _pkg in ("mrs_sim", "mrs_ethical_gate", "mrs_common", "fleet_checker"):
    if str(SRC / _pkg) not in sys.path:
        sys.path.insert(0, str(SRC / _pkg))
