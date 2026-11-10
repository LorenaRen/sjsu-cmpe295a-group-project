"""Gate settings: the node's ROS parameters plus the policy file, checked (pure Python).

Scalars are ROS parameters, so `ros2 param get` shows them and a launch file or the scenario
runner can override them. The access policy is a nested table, which ROS parameters cannot
express, so it is a YAML file named by `policy_file`; the people S-2 checks around are a list,
in the YAML file named by `people_file`. Everything is read once, at start-up: changing speed
or margin halfway through an episode would make its verdicts incomparable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from mrs_common import names as N
from mrs_sim.world_map import resolve_world
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError

from fleet_checker.adapters.team_check import Rules
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation, RegionKind, TaskType
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
    "start_source": (str, "spawns", "where robots start: spawns (the map's spawn slots) or team_state "
                                    "(Module 3's /mrs/team_state)"),
    "team_state_max_age": (float, REQUIRED, "start_source team_state: a position older than this [s], or "
                                            "stamped more than this ahead of the gate's clock, is unknown"),
    "speed": (float, REQUIRED, "nominal driving speed [m/s] used to time each robot's plan"),
    "robot_length": (float, REQUIRED, "robot length [m] along its direction of travel; S-4 counts a robot "
                                      "at a node while any part of it is over the node"),
    "margin": (float, 0.5, "each occupancy interval is widened by this much at both ends [s] (S-1, S-2)"),
    "max_duration": (float, DEFAULT_MAX_DURATION, "longest plan allowed [s] (P-3)"),
    "starvation_threshold": (int, 2, "REJECTs in a row that make a robot starving (F-2's M)"),
    "crowding_radius": (float, REQUIRED, "S-2's R: a robot this close to a person [m] is near them"),
    "crowding_max_robots": (int, REQUIRED, "S-2's N: most robots allowed near one person at once"),
    # Which rules run (rules_v1.md). Grounding and compile errors are always checked. The defaults
    # are the team implementation guide's; config/rules_all.yaml turns everything on.
    "rule_s1": (bool, True, "S-1: zone capacity, with head-on swaps; R-1 picks who gives way"),
    "rule_s2": (bool, False, "S-2: robots crowding a person"),
    "rule_s4": (bool, False, "S-4: node occupancy"),
    "rule_p1": (bool, True, "P-1: no final stop in a passthrough zone"),
    "rule_p2": (bool, True, "P-2: region access"),
    "rule_p3": (bool, False, "P-3: longest plan (max_duration)"),
    "fairness_ledger": (bool, False, "keep the fairness ledger (R-1's yield counts, F-2); off: R-1 ranks by "
                                     "task type, then first arrival, then robot id"),
    "policy_file": (str, "", "access policy and task types (YAML); empty: the package's config/policy.yaml"),
    "people_file": (str, "", "people for S-2 (YAML); empty: the package's config/people.yaml (nobody)"),
}

START_SOURCES = ("spawns", "team_state")


class SettingsError(ValueError):
    """A parameter or the policy file is wrong; the gate must not start."""


class PolicyFile(BaseModel):
    """config/policy.yaml. A zone must be allowed by both tables; a zone in neither is open."""
    model_config = ConfigDict(extra="forbid")

    by_kind: dict[RegionKind, list[TaskType]] = {}
    by_id: dict[str, list[TaskType]] = {}
    task_types: dict[str, TaskType] = {}   # robot_id -> task type; robots not listed are delivery


class _Person(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    x: FiniteFloat   # map frame [m]
    y: FiniteFloat


class PeopleFile(BaseModel):
    """config/people.yaml: the people S-2 checks around, each standing still where listed."""
    model_config = ConfigDict(extra="forbid")

    people: list[_Person] = []


@dataclass(frozen=True)
class GateSettings:
    map_path: Path
    robots: tuple[str, ...]
    start_source: str
    team_state_max_age: float | None   # [s]; only for start_source team_state
    speed: float
    robot_length: float
    margin: float
    max_duration: float
    starvation_threshold: int
    rules: Rules
    fairness_ledger: bool
    crowding: CrowdingLimit
    policy_file: Path
    access_policy: AccessPolicy
    task_types: Mapping[str, TaskType]
    people_file: Path
    people: tuple[HumanObservation, ...]

    def rules_line(self) -> str:
        """Which rules run, for the node's start-up log."""
        ledger = ("on" if self.fairness_ledger
                  else "off (R-1 ranks by task type, then first arrival, then robot id)")
        return (f"rules on: {', '.join(self.rules.enabled()) or 'none'}; "
                f"off: {', '.join(self.rules.disabled()) or 'none'}; fairness ledger {ledger}; "
                "grounding and compile errors are always checked")

    def describe(self) -> dict[str, Any]:
        """Plain JSON values, for the node's start-up log line."""
        return {
            "map": str(self.map_path), "robots": list(self.robots), "start_source": self.start_source,
            "team_state_max_age": self.team_state_max_age,
            "speed": self.speed, "robot_length": self.robot_length, "margin": self.margin,
            "max_duration": self.max_duration,
            "starvation_threshold": self.starvation_threshold,
            "rules": {"on": self.rules.enabled(), "off": self.rules.disabled(),
                      "fairness_ledger": self.fairness_ledger},
            "crowding": {"radius": self.crowding.radius, "max_robots": self.crowding.max_robots},
            "policy_file": str(self.policy_file),
            "access_policy": {
                "by_kind": {k.value: sorted(t.value for t in v) for k, v in self.access_policy.by_kind.items()},
                "by_id": {k: sorted(t.value for t in v) for k, v in self.access_policy.by_id.items()},
            },
            "task_types": {robot: t.value for robot, t in self.task_types.items()},
            "people_file": str(self.people_file),
            "people": [{"id": p.human_id, "x": p.position[0], "y": p.position[1]} for p in self.people],
        }


def load_settings(values: Mapping[str, Any], *, default_policy_file: Path,
                  default_people_file: Path) -> GateSettings:
    """Check the parameter values and read the policy and people files. Raises SettingsError.

    `values` maps parameter names to values; a missing name takes its default and
    None means "not set". The default files are used when policy_file or people_file is empty.
    Whether each person stands inside a zone needs the map: GateSession.from_settings checks it.
    """
    v = {name: values.get(name, None if default is REQUIRED else default)
         for name, (_, default, _) in PARAMETERS.items()}

    if v["speed"] is None:
        raise SettingsError("speed is not set: give the nominal driving speed [m/s] (config/gate.yaml)")
    if v["robot_length"] is None:
        raise SettingsError("robot_length is not set: give the robot's length [m] (config/gate.yaml)")
    for name, what in (("crowding_radius", "S-2's R [m]"), ("crowding_max_robots", "S-2's N")):
        if v[name] is None:
            raise SettingsError(f"{name} is not set: give {what} (config/gate.yaml)")
    _require(v["speed"] > 0, f"speed must be > 0 m/s, got {v['speed']}")
    _require(math.isfinite(v["robot_length"]) and v["robot_length"] > 0,
             f"robot_length must be > 0 m, got {v['robot_length']}")
    _require(math.isfinite(v["crowding_radius"]) and v["crowding_radius"] > 0,
             f"crowding_radius must be > 0 m, got {v['crowding_radius']}")
    _require(v["crowding_max_robots"] >= 1,
             f"crowding_max_robots must be at least 1, got {v['crowding_max_robots']}")
    _require(v["margin"] >= 0, f"margin must be >= 0 s, got {v['margin']}")
    _require(v["max_duration"] > 0, f"max_duration must be > 0 s, got {v['max_duration']}")
    _require(v["starvation_threshold"] >= 1,
             f"starvation_threshold must be at least 1, got {v['starvation_threshold']}")
    for name in _RULE_SWITCHES:
        _require(isinstance(v[name], bool), f"{name} must be true or false, got {v[name]!r}")
    _require(1 <= v["robots"] <= 26, f"robots must be between 1 and 26, got {v['robots']}")
    robots = tuple(N.robot_ids(v["robots"]))

    _require(v["start_source"] in START_SOURCES,
             f"start_source must be one of {list(START_SOURCES)}, got {v['start_source']!r}")
    max_age = v["team_state_max_age"]
    if v["start_source"] == "team_state":
        if max_age is None:
            raise SettingsError("team_state_max_age is not set: give the oldest robot position [s] that "
                                "start_source team_state may use (config/gate.yaml)")
        _require(math.isfinite(max_age) and max_age > 0, f"team_state_max_age must be > 0 s, got {max_age}")
    else:
        max_age = None   # not used

    policy_file = Path(v["policy_file"]).expanduser().resolve() if v["policy_file"] else Path(default_policy_file)
    policy = _read_policy(policy_file)
    outside = sorted(set(policy.task_types) - set(robots))
    _require(not outside, f"policy_file {policy_file}: task_types names robots outside the fleet "
                          f"{list(robots)}: {outside}")
    people_file = Path(v["people_file"]).expanduser().resolve() if v["people_file"] else Path(default_people_file)

    return GateSettings(
        map_path=_map_path(v["world"], v["map_file"]),
        robots=robots,
        start_source=v["start_source"],
        team_state_max_age=None if max_age is None else float(max_age),
        speed=float(v["speed"]),
        robot_length=float(v["robot_length"]),
        margin=float(v["margin"]),
        max_duration=float(v["max_duration"]),
        starvation_threshold=int(v["starvation_threshold"]),
        rules=Rules(**{field: v[name] for name, field in _RULE_SWITCHES.items() if field}),
        fairness_ledger=v["fairness_ledger"],
        crowding=CrowdingLimit(radius=float(v["crowding_radius"]), max_robots=int(v["crowding_max_robots"])),
        policy_file=policy_file,
        access_policy=AccessPolicy(by_kind={k: frozenset(t) for k, t in policy.by_kind.items()},
                                   by_id={k: frozenset(t) for k, t in policy.by_id.items()}),
        task_types=dict(policy.task_types),
        people_file=people_file,
        people=_read_people(people_file),
    )


# parameter -> Rules field (None: not a rule of Rules)
_RULE_SWITCHES = {"rule_s1": "s1", "rule_s2": "s2", "rule_s4": "s4", "rule_p1": "p1", "rule_p2": "p2",
                  "rule_p3": "p3", "fairness_ledger": None}


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


def _read_people(path: Path) -> tuple[HumanObservation, ...]:
    try:
        people = PeopleFile.model_validate(yaml.safe_load(path.read_text()) or {}).people
    except (OSError, yaml.YAMLError, ValidationError) as e:
        raise SettingsError(f"people_file {path}: {e}") from e
    ids = [p.id for p in people]
    shared = sorted({i for i in ids if ids.count(i) > 1})
    if shared:
        raise SettingsError(f"people_file {path}: two people have the id {shared[0]!r}")
    return tuple(HumanObservation(human_id=p.id, position=(p.x, p.y)) for p in people)
