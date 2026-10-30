"""World maps (Module 2). Pure Python, no ROS imports.

A world is a pair of files with the same base name in mrs_sim/worlds/:

  <name>.sdf    the Gazebo world: walls, floor, lights. Its <world name="..."> must be <name>.
  <name>.yaml   the map the rest of the stack reasons over: waypoint-graph nodes and edges,
                semantic zones and the robot spawn slots.

Switching worlds is `world:=<name>` on the launch file; adding one is dropping a new pair into
worlds/ (see docs/USER_GUIDE.md §4.2). Everything else (robots, bridge, launch) is shared.
"""
from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mrs_common import names as N

PACKAGE = "mrs_sim"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Node(_Strict):
    x: float
    y: float


class Rect(_Strict):
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @model_validator(mode="after")
    def _ordered(self) -> "Rect":
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("rect needs x_min < x_max and y_min < y_max")
        return self

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


class Zone(_Strict):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1, description="e.g. room, narrow, keep_clear, restricted")
    rect: Rect
    capacity: Optional[int] = Field(default=None, ge=1, description="max robots inside at once; None = no limit")
    description: str = ""


class Spawn(_Strict):
    node: str
    yaw: float = 0.0  # [rad], map frame


class Pose2D(_Strict):
    x: float
    y: float
    yaw: float


class WorldMap(_Strict):
    world: str = Field(min_length=1, description="must equal <world name> in the .sdf")
    description: str = ""
    nodes: dict[str, Node]
    edges: list[tuple[str, str]]
    zones: list[Zone] = []
    spawns: list[Spawn] = Field(min_length=1, description="robot_a takes the first slot, robot_b the second, ...")

    @model_validator(mode="after")
    def _references(self) -> "WorldMap":
        for n in self.nodes:
            N._check_node(n)
        seen = set()
        for a, b in self.edges:
            for n in (a, b):
                if n not in self.nodes:
                    raise ValueError(f"edge {a}--{b} uses unknown node '{n}'")
            e = N.edge_id(a, b)
            if e in seen:
                raise ValueError(f"duplicate edge {e}")
            seen.add(e)
        zone_ids = [z.id for z in self.zones]
        if len(set(zone_ids)) != len(zone_ids):
            raise ValueError(f"duplicate zone ids in {zone_ids}")
        spawn_nodes = [s.node for s in self.spawns]
        for n in spawn_nodes:
            if n not in self.nodes:
                raise ValueError(f"spawn uses unknown node '{n}'")
        if len(set(spawn_nodes)) != len(spawn_nodes):
            raise ValueError("two spawn slots share a node")
        return self

    # ---- queries used by the launch file and later modules ------------------------------------
    def edge_ids(self) -> list[str]:
        """Undirected edge ids (names.edge_id), the ids blocked_edges and avoid_edge use."""
        return [N.edge_id(a, b) for a, b in self.edges]

    def neighbors(self, node: str) -> list[str]:
        return sorted({b if a == node else a for a, b in self.edges if node in (a, b)})

    def zones_at(self, x: float, y: float) -> list[str]:
        """Ids of every zone containing (x, y): what WorldState.zones reports."""
        return [z.id for z in self.zones if z.rect.contains(x, y)]

    def zone(self, zone_id: str) -> Zone:
        for z in self.zones:
            if z.id == zone_id:
                return z
        raise KeyError(zone_id)

    def nearest_node(self, x: float, y: float) -> tuple[str, float]:
        """(node, distance) of the closest graph node."""
        return min(((n, math.hypot(p.x - x, p.y - y)) for n, p in self.nodes.items()), key=lambda t: t[1])

    def spawn_poses(self, robot_ids: list[str]) -> dict[str, Pose2D]:
        if len(robot_ids) > len(self.spawns):
            raise ValueError(f"world '{self.world}' has {len(self.spawns)} spawn slots, "
                             f"asked for {len(robot_ids)} robots")
        out = {}
        for r, s in zip(robot_ids, self.spawns):
            p = self.nodes[s.node]
            out[N.validate_robot_id(r)] = Pose2D(x=p.x, y=p.y, yaw=s.yaw)
        return out

    def to_prompt_dict(self) -> dict:
        """Compact static map for an LLM system prompt (Module 4)."""
        return {
            "nodes": {n: [p.x, p.y] for n, p in self.nodes.items()},
            "edges": self.edge_ids(),
            "zones": [{"id": z.id, "type": z.type, **({"capacity": z.capacity} if z.capacity else {}),
                       **({"description": z.description} if z.description else {})} for z in self.zones],
        }


# ---- finding and loading worlds -----------------------------------------------------------------
def worlds_dir() -> Path:
    """The source tree's worlds/ when running from it (pytest), else the installed share dir."""
    src = Path(__file__).resolve().parents[1] / "worlds"
    if src.is_dir():
        return src
    from ament_index_python.packages import get_package_share_directory

    return Path(get_package_share_directory(PACKAGE)) / "worlds"


def list_worlds() -> list[str]:
    d = worlds_dir()
    return sorted(p.stem for p in d.glob("*.sdf") if p.with_suffix(".yaml").exists())


def resolve_world(world: str) -> tuple[Path, Path]:
    """(sdf, yaml) for a world name ('narrow_aisle') or a path to a .sdf with a .yaml next to it."""
    p = Path(world).expanduser()
    if p.suffix == ".sdf":
        sdf = p.resolve()
    else:
        sdf = worlds_dir() / f"{world}.sdf"
    yml = sdf.with_suffix(".yaml")
    if not sdf.exists():
        raise FileNotFoundError(f"no world '{world}' ({sdf}); available: {list_worlds()}")
    if not yml.exists():
        raise FileNotFoundError(f"world '{world}' has no map file {yml.name} next to {sdf.name}")
    return sdf, yml


def sdf_world_name(sdf: Path) -> str:
    w = ET.parse(sdf).getroot().find("world")
    if w is None or not w.get("name"):
        raise ValueError(f"{sdf} has no <world name=...>")
    return w.get("name")


def load_world_map(world: str) -> tuple[WorldMap, Path]:
    """Load and validate a world. Returns (map, sdf_path)."""
    sdf, yml = resolve_world(world)
    with open(yml) as f:
        wm = WorldMap.model_validate(yaml.safe_load(f))
    if wm.world != sdf.stem or sdf_world_name(sdf) != wm.world:
        raise ValueError(f"names disagree: file '{sdf.stem}', yaml world '{wm.world}', "
                         f"sdf <world name='{sdf_world_name(sdf)}'>; all three must match")
    return wm, sdf


def list_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="List mrs_sim worlds and check each one loads.")
    ap.parse_args(argv)
    rc = 0
    for name in list_worlds():
        try:
            wm, _ = load_world_map(name)
            print(f"[ok]   {name}: {len(wm.nodes)} nodes, {len(wm.edges)} edges, "
                  f"{len(wm.zones)} zones, {len(wm.spawns)} spawn slots")
        except Exception as e:  # noqa: BLE001 - report every broken world, keep going
            print(f"[FAIL] {name}: {e}")
            rc = 1
    return rc
