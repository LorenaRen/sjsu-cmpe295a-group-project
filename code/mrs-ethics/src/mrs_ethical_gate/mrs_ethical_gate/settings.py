"""Gate settings: the node's ROS parameters plus the policy file, checked (pure Python).

Scalars are ROS parameters, so `ros2 param get` shows them and a launch file or the scenario
runner can override them. The access policy is a nested table, which ROS parameters cannot
express, so it is a YAML file named by `policy_file`. Everything is read once, at start-up:
changing speed or margin halfway through an episode would make its verdicts incomparable.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from mrs_common import names as N
from mrs_sim.world_map import resolve_world
from pydantic import BaseModel, ConfigDict, ValidationError

from fleet_checker.plan import RegionKind, TaskType
from fleet_checker.policy import DEFAULT_MAX_DURATION, AccessPolicy


class _Required:
    def __repr__(self) -> str:
        return "REQUIRED"


REQUIRED = _Required()   # a parameter with no default: the node refuses to start without it

# name -> (type, default, description). The node declares exactly these, read-only.
PARAMETERS: dict[str, tuple[type, Any, str]] = {
    "world": (str, "narrow_aisle",
              "mrs_sim world name, or a path to a .sdf with its .yaml next to it (as in sim.launch.py)"),
    "map_file": (str, "", "a map .yaml to use instead of world, e.g. a test map that has no .sdf"),
    "robots": (int, 2, "fleet size: robot_a, robot_b, ... at the map's spawn slots, as in sim.launch.py"),
    "start_source": (str, "spawns", "where robots start: spawns (team_state needs Module 3, not built)"),
    "speed": (float, REQUIRED, "nominal driving speed [m/s] used to time each robot's plan"),
    "margin": (float, 0.5, "each occupancy interval is widened by this much at both ends [s] (S-1)"),
    "max_duration": (float, DEFAULT_MAX_DURATION, "longest plan allowed [s] (P-3)"),
    "starvation_threshold": (int, 3, "REJECTs in a row that make a robot starving (F-2's M)"),
    "policy_file": (str, "", "access policy and task types (YAML); empty: the package's config/policy.yaml"),
}

START_SOURCES = ("spawns",)


class SettingsError(ValueError):
    """A parameter or the policy file is wrong; the gate must not start."""


class PolicyFile(BaseModel):
    """config/policy.yaml. A zone must be allowed by both tables; a zone in neither is open."""
    model_config = ConfigDict(extra="forbid")

    by_kind: dict[RegionKind, list[TaskType]] = {}
    by_id: dict[str, list[TaskType]] = {}
    task_types: dict[str, TaskType] = {}   # robot_id -> task type; robots not listed are delivery


@dataclass(frozen=True)
class GateSettings:
    map_path: Path
    robots: tuple[str, ...]
    start_source: str
    speed: float
    margin: float
    max_duration: float
    starvation_threshold: int
    policy_file: Path
    access_policy: AccessPolicy
    task_types: Mapping[str, TaskType]

    def describe(self) -> dict[str, Any]:
        """Plain JSON values, for the node's start-up log line."""
        return {
            "map": str(self.map_path), "robots": list(self.robots), "start_source": self.start_source,
            "speed": self.speed, "margin": self.margin, "max_duration": self.max_duration,
            "starvation_threshold": self.starvation_threshold, "policy_file": str(self.policy_file),
            "access_policy": {
                "by_kind": {k.value: sorted(t.value for t in v) for k, v in self.access_policy.by_kind.items()},
                "by_id": {k: sorted(t.value for t in v) for k, v in self.access_policy.by_id.items()},
            },
            "task_types": {robot: t.value for robot, t in self.task_types.items()},
        }


def load_settings(values: Mapping[str, Any], *, default_policy_file: Path) -> GateSettings:
    """Check the parameter values and read the policy file. Raises SettingsError.

    `values` maps parameter names to values; a missing name takes its default and
    None means "not set". `default_policy_file` is used when policy_file is empty.
    """
    v = {name: values.get(name, None if default is REQUIRED else default)
         for name, (_, default, _) in PARAMETERS.items()}

    if v["speed"] is None:
        raise SettingsError("speed is not set: give the nominal driving speed [m/s] (config/gate.yaml)")
    _require(v["speed"] > 0, f"speed must be > 0 m/s, got {v['speed']}")
    _require(v["margin"] >= 0, f"margin must be >= 0 s, got {v['margin']}")
    _require(v["max_duration"] > 0, f"max_duration must be > 0 s, got {v['max_duration']}")
    _require(v["starvation_threshold"] >= 1,
             f"starvation_threshold must be at least 1, got {v['starvation_threshold']}")
    _require(1 <= v["robots"] <= 26, f"robots must be between 1 and 26, got {v['robots']}")
    robots = tuple(N.robot_ids(v["robots"]))

    if v["start_source"] == "team_state":
        raise SettingsError("start_source team_state is not built yet: it needs Module 3's "
                            "/mrs/team_state; use spawns")
    _require(v["start_source"] in START_SOURCES,
             f"start_source must be one of {list(START_SOURCES)}, got {v['start_source']!r}")

    policy_file = Path(v["policy_file"]).expanduser().resolve() if v["policy_file"] else Path(default_policy_file)
    policy = _read_policy(policy_file)
    outside = sorted(set(policy.task_types) - set(robots))
    _require(not outside, f"policy_file {policy_file}: task_types names robots outside the fleet "
                          f"{list(robots)}: {outside}")

    return GateSettings(
        map_path=_map_path(v["world"], v["map_file"]),
        robots=robots,
        start_source=v["start_source"],
        speed=float(v["speed"]),
        margin=float(v["margin"]),
        max_duration=float(v["max_duration"]),
        starvation_threshold=int(v["starvation_threshold"]),
        policy_file=policy_file,
        access_policy=AccessPolicy(by_kind={k: frozenset(t) for k, t in policy.by_kind.items()},
                                   by_id={k: frozenset(t) for k, t in policy.by_id.items()}),
        task_types=dict(policy.task_types),
    )


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise SettingsError(message)


def _map_path(world: str, map_file: str) -> Path:
    if map_file:
        path = Path(map_file).expanduser().resolve()
        _require(path.is_file(), f"map_file {path} does not exist")
        return path
    try:
        return resolve_world(world)[1]
    except FileNotFoundError as e:
        raise SettingsError(f"world: {e}") from e


def _read_policy(path: Path) -> PolicyFile:
    try:
        return PolicyFile.model_validate(yaml.safe_load(path.read_text()) or {})
    except (OSError, yaml.YAMLError, ValidationError) as e:
        raise SettingsError(f"policy_file {path}: {e}") from e
