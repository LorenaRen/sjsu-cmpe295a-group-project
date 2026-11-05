"""Reproducible benchmarks of the shared checker (run.py): how long a check takes, and how far
the gate's constraints get a scripted planner. Pure Python: the checker and the gate are
imported from the source tree, with no ROS, so importing this package puts their source
directories on sys.path (as the packages' own pytest.ini files do)."""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
for _pkg in ("mrs_sim", "mrs_ethical_gate", "mrs_common", "fleet_checker"):
    if str(_SRC / _pkg) not in sys.path:
        sys.path.insert(0, str(_SRC / _pkg))
