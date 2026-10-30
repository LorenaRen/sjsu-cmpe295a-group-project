"""mrs_sim world map (Module 2) -> checker WorldMap, plus the waypoint graph the compiler routes on.

A map file (mrs_sim/worlds/<name>.yaml) has a waypoint graph (nodes, edges) and
axis-aligned semantic zones. The checker reasons about regions, so:

* every zone becomes a Region with the same id. Capacity comes from the map
  (None = no limit); the zone type picks the RegionKind (ZONE_KINDS).
* every edge is cut where it crosses a zone boundary (segment clipping), so the
  compiler knows how far a robot drives inside each zone. Looking only at which
  zone each node is in would get this wrong twice: robots cross boundaries
  partway along an edge, and an edge can pass through a zone that has no node
  (a keep-clear strip in a doorway).
* two zones are adjacent when an edge passes from one straight into the other.

Checked when the map is loaded; a map that breaks one is rejected, naming the
zone, node or edge:
  - zone interiors do not overlap (sharing a boundary is fine);
  - every node is in exactly one zone, so not on a boundary two zones share;
  - every edge runs through zones for its whole length.
Nested zones (a keep-clear area inside a room) therefore cannot be loaded yet.

This reads the YAML format, not mrs_sim's classes, so it works before mrs_sim is
merged; a loaded mrs_sim WorldMap can be passed in as wm.model_dump().
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Sequence

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..plan import Region, RegionKind, WorldMap

# mrs_sim zone type -> checker region kind. A type missing here is rejected:
# how a new kind of zone should be checked is a decision, not a default.
ZONE_KINDS: dict[str, RegionKind] = {
    "room": RegionKind.OPEN_AREA,
    "narrow": RegionKind.AISLE,
    "keep_clear": RegionKind.KEEP_CLEAR,
    "restricted": RegionKind.RESTRICTED,
}

# Stands in for mrs_sim's capacity None (no limit): more than any fleet, so S-1
# never fires there, and Region.capacity can stay a plain positive int.
NO_LIMIT = 1_000_000

_EPS = 1e-9  # metres and segment parameters: rounding at shared boundaries


class MapError(ValueError):
    """The file parses, but the map breaks one of the assumptions above."""


# --------------------------------------------------------------------------
# The file format (mirrors mrs_sim.world_map; unknown keys are an error, so a
# format change shows up here instead of being silently ignored)
# --------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Node(_Strict):
    x: float
    y: float


class _Rect(_Strict):
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    @model_validator(mode="after")
    def _ordered(self) -> "_Rect":
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("rect needs x_min < x_max and y_min < y_max")
        return self

    def contains(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


class _Zone(_Strict):
    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    rect: _Rect
    capacity: int | None = Field(default=None, ge=1)
    description: str = ""


class _Spawn(_Strict):
    node: str
    yaw: float = 0.0


class _MapFile(_Strict):
    world: str = Field(min_length=1)
    description: str = ""
    nodes: dict[str, _Node]
    edges: list[tuple[str, str]]
    zones: list[_Zone] = []
    spawns: list[_Spawn] = Field(min_length=1)


# --------------------------------------------------------------------------
# What the compiler and the checks use
# --------------------------------------------------------------------------

class EdgePiece(NamedTuple):
    zone: str        # zone id, which is also the region id
    length: float    # metres driven inside that zone


@dataclass(frozen=True)
class SimMap:
    name: str
    world: WorldMap                                       # zones as checker regions
    positions: dict[str, tuple[float, float]]             # node -> (x, y), map frame [m]
    zone_of: dict[str, str]                               # node -> the zone it is in
    neighbors: dict[str, dict[str, float]]                # node -> {neighbor: edge length [m]}
    pieces: dict[tuple[str, str], tuple[EdgePiece, ...]]  # (from, to) -> zones in driving order
    spawns: tuple[str, ...]                               # spawn nodes, in slot order


def load_sim_map(path: str | Path,
                 zone_kinds: Mapping[str, RegionKind] = ZONE_KINDS) -> SimMap:
    """Load an mrs_sim map file (<world>.yaml)."""
    with open(path) as f:
        return sim_map_from_dict(yaml.safe_load(f), zone_kinds)


def sim_map_from_dict(data: Mapping[str, Any],
                      zone_kinds: Mapping[str, RegionKind] = ZONE_KINDS) -> SimMap:
    m = _MapFile.model_validate(data)
    _check_zones(m, zone_kinds)

    positions = {n: (p.x, p.y) for n, p in m.nodes.items()}
    zone_of = {n: _zone_of_node(m, n, xy) for n, xy in positions.items()}

    neighbors: dict[str, dict[str, float]] = {n: {} for n in m.nodes}
    pieces: dict[tuple[str, str], tuple[EdgePiece, ...]] = {}
    adjacent: dict[str, set[str]] = {z.id: set() for z in m.zones}
    for a, b in m.edges:
        for n in (a, b):
            if n not in m.nodes:
                raise MapError(f"edge {a}--{b} uses unknown node {n!r}")
        if a == b:
            raise MapError(f"edge {a}--{b} connects a node to itself")
        if b in neighbors[a]:
            raise MapError(f"duplicate edge {a}--{b}")
        length = math.dist(positions[a], positions[b])
        if length <= _EPS:
            raise MapError(f"edge {a}--{b} has zero length")
        forward = _split_edge(m, a, b, length)
        neighbors[a][b] = neighbors[b][a] = length
        pieces[(a, b)] = forward
        pieces[(b, a)] = tuple(reversed(forward))
        for p, q in pairwise(forward):
            adjacent[p.zone].add(q.zone)
            adjacent[q.zone].add(p.zone)

    for s in m.spawns:
        if s.node not in m.nodes:
            raise MapError(f"spawn uses unknown node {s.node!r}")

    regions = {
        z.id: Region(
            id=z.id,
            kind=zone_kinds[z.type],
            capacity=NO_LIMIT if z.capacity is None else z.capacity,
            center=((z.rect.x_min + z.rect.x_max) / 2, (z.rect.y_min + z.rect.y_max) / 2),
            neighbors=frozenset(adjacent[z.id]),
        )
        for z in m.zones
    }
    return SimMap(
        name=m.world,
        world=WorldMap(regions=regions),
        positions=positions,
        zone_of=zone_of,
        neighbors=neighbors,
        pieces=pieces,
        spawns=tuple(s.node for s in m.spawns),
    )


def spawn_starts(sim_map: SimMap, robot_ids: Sequence[str]) -> dict[str, str]:
    """robot_ids[i] starts at spawn slot i, the way mrs_sim's launch file places robots."""
    if len(robot_ids) > len(sim_map.spawns):
        raise ValueError(f"map {sim_map.name!r} has {len(sim_map.spawns)} spawn slots, "
                         f"asked for {len(robot_ids)} robots")
    return dict(zip(robot_ids, sim_map.spawns))


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

def _check_zones(m: _MapFile, zone_kinds: Mapping[str, RegionKind]) -> None:
    ids = [z.id for z in m.zones]
    if len(set(ids)) != len(ids):
        raise MapError(f"duplicate zone ids in {ids}")
    for z in m.zones:
        if z.type not in zone_kinds:
            raise MapError(f"zone {z.id!r} has unknown zone type {z.type!r}; "
                           f"known: {sorted(zone_kinds)}")
    for a, b in combinations(m.zones, 2):
        ra, rb = a.rect, b.rect
        if (ra.x_min < rb.x_max - _EPS and rb.x_min < ra.x_max - _EPS
                and ra.y_min < rb.y_max - _EPS and rb.y_min < ra.y_max - _EPS):
            raise MapError(f"zones {a.id!r} and {b.id!r} overlap; "
                           f"nested or overlapping zones are not supported yet")


def _zone_of_node(m: _MapFile, node: str, xy: tuple[float, float]) -> str:
    inside = [z.id for z in m.zones if z.rect.contains(*xy)]
    if not inside:
        raise MapError(f"node {node!r} at {xy} is outside every zone")
    if len(inside) > 1:
        raise MapError(f"node {node!r} at {xy} is on the boundary of zones {inside}; "
                       f"move it inside one of them")
    return inside[0]


def _split_edge(m: _MapFile, a: str, b: str, length: float) -> tuple[EdgePiece, ...]:
    """The zones the straight segment a->b passes through, in order, with the distance in each."""
    pa, pb = (m.nodes[a].x, m.nodes[a].y), (m.nodes[b].x, m.nodes[b].y)
    spans = []
    for z in m.zones:
        span = _clip(pa, pb, z.rect)
        if span is not None and span[1] - span[0] > _EPS:
            spans.append((span[0], span[1], z.id))
    spans.sort()

    def at(t: float) -> str:
        return f"({pa[0] + t * (pb[0] - pa[0]):.2f}, {pa[1] + t * (pb[1] - pa[1]):.2f})"

    out: list[EdgePiece] = []
    t = 0.0
    for t0, t1, zone in spans:
        if t0 > t + _EPS:
            raise MapError(f"edge {a}--{b} leaves every zone between {at(t)} and {at(t0)}")
        if t0 < t - _EPS:
            raise MapError(f"edge {a}--{b} is in zones {out[-1].zone!r} and {zone!r} at once "
                           f"(it runs along their shared boundary)")
        end = 1.0 if t1 > 1.0 - _EPS else t1
        out.append(EdgePiece(zone, (end - t) * length))
        t = end
    if t < 1.0:
        raise MapError(f"edge {a}--{b} leaves every zone between {at(t)} and {at(1.0)}")
    return tuple(out)


def _clip(pa: tuple[float, float], pb: tuple[float, float], r: _Rect) -> tuple[float, float] | None:
    """Liang-Barsky clipping: the range [t0, t1] of t for which pa + t * (pb - pa)
    is inside rectangle r (boundary included), or None if the segment misses it."""
    (ax, ay), (bx, by) = pa, pb
    dx, dy = bx - ax, by - ay
    t0, t1 = 0.0, 1.0
    # Each pair is one side of the rectangle: the point is inside that side while p * t <= q.
    for p, q in ((-dx, ax - r.x_min), (dx, r.x_max - ax), (-dy, ay - r.y_min), (dy, r.y_max - ay)):
        if p == 0:
            if q < 0:
                return None              # parallel to this side and outside it
        elif p < 0:
            t0 = max(t0, q / p)          # crossing this side inwards
        else:
            t1 = min(t1, q / p)          # crossing this side outwards
    return (t0, t1) if t0 <= t1 else None
