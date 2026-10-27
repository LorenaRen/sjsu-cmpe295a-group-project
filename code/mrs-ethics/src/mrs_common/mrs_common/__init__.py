"""Shared code for the MRS ethical gating project (Module 1).

Pure-Python modules (importable without ROS): plan_schema, names, ids, reasons, samples.
ROS-dependent modules (need a built workspace): convert, qos.qos_for, tools.
"""
from importlib import resources as _resources

from .plan_schema import (SCHEMA_VERSION, Action, Assignment, Constraint, Task, TeamPlan,  # noqa: F401
                          check_references, constraints_from_json, constraints_to_json, hold_all_plan,
                          plan_from_json, plan_to_json, team_plan_json_schema)


def sample_path(name: str) -> str:
    """Path of a bundled sample plan, e.g. sample_path('valid_two_robots.json')."""
    return str(_resources.files(__package__).joinpath("samples", name))


def load_sample(name: str) -> str:
    return _resources.files(__package__).joinpath("samples", name).read_text()
