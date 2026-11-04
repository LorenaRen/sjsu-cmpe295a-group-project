"""Snapshot files for fake_team_state: where it says the robots are (pure Python, no ROS).

  description: what the snapshot shows
  map: samples_world            # the world name of the map it is written for
  robots:                       # one WorldState each; yaw defaults to 0
    - {robot_id: robot_a, at: west_s, x: 0.0, y: -1.2}
    - {robot_id: robot_b, at: corridor_1->pass_e, x: 8.6, y: 0.0}
  blocked_edges: []             # TeamState.blocked_edges, as edge ids ("corridor_1--room_3")

Unknown keys are an error, and so are two robots of one id. Whether each `at` is a node or
an edge of the map is not checked here: fake_team_state stands in for Module 3 and publishes
what the file says, and judging it is the gate's job (starts.py), which these tools do not
share code with. Only the map name is checked, as for scenarios.

A `snapshot` parameter names a file (a path: it has a "/" or ends in .yaml) or one of this
package's snapshots (a bare name, looked up in the snapshots/ directory).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, ValidationError, model_validator

from .run_settings import _map_path
from .scenarios import ScenarioError, _first_error

# name -> (type, default, description)
PARAMETERS: dict[str, tuple[type, Any, str]] = {
    "snapshot": (str, "", "a snapshot file, or the name of one of this package's snapshots (required)"),
    "world": (str, "narrow_aisle", "the map, as for the gate: a world name in mrs_sim/worlds, or a path to a .sdf"),
    "map_file": (str, "", "the map, as for the gate: a map .yaml to use instead of world"),
    "rate": (float, 2.0, "TeamStates per second [Hz]; Module 3 publishes WorldState at 2 Hz"),
    "stop_after": (float, 0.0, "stop publishing and exit after this many seconds; 0: publish until stopped"),
    "episode_id": (str, "", "episode_id of every message; empty: none, which the gate takes for any episode"),
}


class _Robot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    robot_id: str = Field(min_length=1)
    at: str = Field(min_length=1)     # a node, or "a->b" between two nodes
    x: FiniteFloat                    # map frame [m]
    y: FiniteFloat
    yaw: FiniteFloat = 0.0            # [rad]


class _SnapshotFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    map: str = Field(min_length=1)
    robots: list[_Robot] = Field(min_length=1)
    blocked_edges: list[str] = []

    @model_validator(mode="after")
    def _one_robot_per_id(self) -> "_SnapshotFile":
        ids = [r.robot_id for r in self.robots]
        twice = sorted({i for i in ids if ids.count(i) > 1})
        if twice:
            raise ValueError(f"two robots have the id {twice[0]!r}")
        return self


@dataclass(frozen=True)
class SnapshotRobot:
    robot_id: str
    at: str
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class Snapshot:
    name: str            # the file name without .yaml
    path: Path
    description: str
    map: str
    robots: tuple[SnapshotRobot, ...]
    blocked_edges: tuple[str, ...]


@dataclass(frozen=True)
class FeedSettings:
    snapshot: Snapshot
    map_path: Path
    rate: float          # [Hz]
    stop_after: float    # [s]; 0: never
    episode_id: str

    def describe(self) -> dict[str, Any]:
        """Plain JSON values, for the node's start-up log line."""
        return {"snapshot": self.snapshot.name, "snapshot_file": str(self.snapshot.path),
                "map": str(self.map_path), "rate": self.rate, "stop_after": self.stop_after,
                "episode_id": self.episode_id,
                "robots": {r.robot_id: {"at": r.at, "x": r.x, "y": r.y} for r in self.snapshot.robots}}


def load_snapshot(snapshot: str, snapshots_dir: Path) -> Snapshot:
    """The snapshot a `snapshot` parameter names (a path, or a name in snapshots_dir)."""
    path = _path(snapshot, snapshots_dir)
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ScenarioError(f"snapshot {path} is not YAML: {e}") from e
    try:
        f = _SnapshotFile.model_validate(data if data is not None else {})
    except ValidationError as e:
        raise ScenarioError(f"snapshot {path}: {_first_error(e)}") from e
    return Snapshot(name=path.stem, path=path, description=f.description.strip(), map=f.map,
                    robots=tuple(SnapshotRobot(r.robot_id, r.at, r.x, r.y, r.yaw) for r in f.robots),
                    blocked_edges=tuple(f.blocked_edges))


def load_feed(values: Mapping[str, Any], *, snapshots_dir: Path) -> FeedSettings:
    """values: every parameter of PARAMETERS. Raises ScenarioError for anything that cannot run."""
    snapshot = load_snapshot(values["snapshot"], snapshots_dir)
    map_path = _map_path(values["world"], values["map_file"])
    try:
        map_name = (yaml.safe_load(map_path.read_text()) or {}).get("world")
    except (OSError, yaml.YAMLError) as e:
        raise ScenarioError(f"map {map_path}: {e}") from e
    if snapshot.map != map_name:
        raise ScenarioError(f"snapshot {snapshot.name} is written for map {snapshot.map!r}, not {map_name!r}: "
                            "its node names would not exist there")
    rate, stop_after = values["rate"], values["stop_after"]
    if not (math.isfinite(rate) and rate > 0):
        raise ScenarioError(f"rate must be more than 0 Hz, got {rate}")
    if not (math.isfinite(stop_after) and stop_after >= 0):
        raise ScenarioError(f"stop_after must be 0 s or more, got {stop_after}")
    return FeedSettings(snapshot=snapshot, map_path=map_path, rate=float(rate), stop_after=float(stop_after),
                        episode_id=values["episode_id"])


def snapshot_names(snapshots_dir: Path) -> list[str]:
    return sorted(p.stem for p in Path(snapshots_dir).glob("*.yaml"))


def _path(snapshot: str, snapshots_dir: Path) -> Path:
    there = ", ".join(snapshot_names(snapshots_dir)) or "none"
    if not snapshot:
        raise ScenarioError(f"no snapshot given: name one of this package's ({there}) or a file")
    if "/" in snapshot or snapshot.endswith((".yaml", ".yml")):
        path = Path(snapshot).expanduser()
    else:
        path = Path(snapshots_dir) / f"{snapshot}.yaml"
    if not path.is_file():
        raise ScenarioError(f"snapshot {snapshot!r} not found ({path}); this package has: {there}")
    return path.resolve()
