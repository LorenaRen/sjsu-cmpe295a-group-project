"""World maps and their SDFs agree (Module 2). Pure Python.

The parametrized checks run on EVERY world in worlds/, so a new world (e.g. aisle_bypass) is
covered as soon as its .sdf + .yaml pair is added.
"""
import math
import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError

from mrs_common import names as N
from mrs_sim.world_map import WorldMap, list_worlds, load_world_map, resolve_world

WORLDS = list_worlds()
NODE_CLEARANCE = 0.30  # [m] robot footprint radius: a robot parked on a node must not touch a wall
EDGE_CLEARANCE = 0.20  # [m] robot half width: driving an edge must not scrape a wall


def _wall_boxes(sdf_path):
    """(x_min, x_max, y_min, y_max) of every axis-aligned box collision in static models."""
    boxes = []
    for model in ET.parse(sdf_path).getroot().iter("model"):
        if (model.findtext("static") or "").strip() != "true":
            continue
        mx, my = [float(v) for v in (model.findtext("pose") or "0 0 0 0 0 0").split()[:2]]
        for col in model.iter("collision"):
            size = col.find("geometry/box/size")
            if size is None:
                continue
            x, y, _z, _r, _p, yaw = [float(v) for v in (col.findtext("pose") or "0 0 0 0 0 0").split()]
            assert abs(yaw) < 1e-6, f"{col.get('name')}: rotated walls are not checked; keep walls axis-aligned"
            sx, sy, _sz = [float(v) for v in size.text.split()]
            boxes.append((mx + x - sx / 2, mx + x + sx / 2, my + y - sy / 2, my + y + sy / 2))
    return boxes


def _dist_to_box(x, y, b):
    dx = max(b[0] - x, 0.0, x - b[1])
    dy = max(b[2] - y, 0.0, y - b[3])
    return math.hypot(dx, dy)


def test_narrow_aisle_is_shipped():
    assert "narrow_aisle" in WORLDS


@pytest.mark.parametrize("world", WORLDS)
def test_world_loads(world):
    wm, sdf = load_world_map(world)
    assert wm.world == world == sdf.stem
    assert len(wm.spawns) >= 2, "every world must fit at least the 2-robot 295A setup"


@pytest.mark.parametrize("world", WORLDS)
def test_graph_is_connected(world):
    wm, _ = load_world_map(world)
    start = next(iter(wm.nodes))
    seen, todo = {start}, [start]
    while todo:
        for n in wm.neighbors(todo.pop()):
            if n not in seen:
                seen.add(n)
                todo.append(n)
    assert seen == set(wm.nodes), f"unreachable nodes: {set(wm.nodes) - seen}"


@pytest.mark.parametrize("world", WORLDS)
def test_nodes_and_edges_clear_of_walls(world):
    wm, sdf = load_world_map(world)
    boxes = _wall_boxes(sdf)
    assert boxes, "no wall collisions found"
    for name, p in wm.nodes.items():
        d = min(_dist_to_box(p.x, p.y, b) for b in boxes)
        assert d >= NODE_CLEARANCE, f"node {name} is {d:.2f} m from a wall"
    for a, b in wm.edges:
        pa, pb = wm.nodes[a], wm.nodes[b]
        for i in range(51):
            t = i / 50
            x, y = pa.x + t * (pb.x - pa.x), pa.y + t * (pb.y - pa.y)
            d = min(_dist_to_box(x, y, box) for box in boxes)
            assert d >= EDGE_CLEARANCE, f"edge {a}--{b} passes {d:.2f} m from a wall at ({x:.2f}, {y:.2f})"


@pytest.mark.parametrize("world", WORLDS)
def test_every_node_is_in_a_zone(world):
    wm, _ = load_world_map(world)
    for name, p in wm.nodes.items():
        assert wm.zones_at(p.x, p.y), f"node {name} is outside every zone"


# ---- narrow_aisle specifics ------------------------------------------------------------------
def test_narrow_aisle_layout():
    wm, _ = load_world_map("narrow_aisle")
    aisle = wm.zone("aisle")
    assert aisle.type == "narrow" and aisle.capacity == 1
    assert aisle.rect.y_max - aisle.rect.y_min <= 2 * 0.40, "two 0.40 m robots must not fit side by side"
    assert wm.zones_at(6.0, 0.0) == ["aisle"]
    assert N.edge_id("aisle_mid", "aisle_w") in wm.edge_ids()
    assert wm.nearest_node(5.9, 0.1)[0] == "aisle_mid"
    poses = wm.spawn_poses(N.robot_ids(2))
    assert poses["robot_a"].x < aisle.rect.x_min < aisle.rect.x_max < poses["robot_b"].x


def test_too_many_robots():
    wm, _ = load_world_map("narrow_aisle")
    with pytest.raises(ValueError, match="spawn slots"):
        wm.spawn_poses(N.robot_ids(len(wm.spawns) + 1))


def test_prompt_dict():
    wm, _ = load_world_map("narrow_aisle")
    d = wm.to_prompt_dict()
    assert set(d) == {"nodes", "edges", "zones"}
    assert {"id": "aisle", "type": "narrow", "capacity": 1,
            "description": wm.zone("aisle").description} in d["zones"]


# ---- validation ----------------------------------------------------------------------------
_BASE = {"world": "w", "nodes": {"a": {"x": 0, "y": 0}, "b": {"x": 1, "y": 0}}, "edges": [["a", "b"]],
         "spawns": [{"node": "a"}]}


@pytest.mark.parametrize("patch", [
    {"edges": [["a", "zz"]]},                                   # unknown node
    {"edges": [["a", "b"], ["b", "a"]]},                        # duplicate (undirected)
    {"spawns": [{"node": "zz"}]},                               # unknown spawn node
    {"spawns": [{"node": "a"}, {"node": "a"}]},                 # shared spawn
    {"nodes": {"a--b": {"x": 0, "y": 0}}, "edges": []},         # bad node name
    {"zones": [{"id": "z", "type": "room", "rect": {"x_min": 1, "x_max": 0, "y_min": 0, "y_max": 1}}]},
    {"extra": 1},                                               # unknown field
])
def test_invalid_maps(patch):
    with pytest.raises(ValidationError):
        WorldMap.model_validate({**_BASE, **patch})


def test_unknown_world():
    with pytest.raises(FileNotFoundError, match="available"):
        resolve_world("no_such_world")


def test_world_by_path(tmp_path):
    _, sdf = load_world_map("narrow_aisle")
    (tmp_path / "copy.sdf").write_text(sdf.read_text())
    with pytest.raises(FileNotFoundError, match="no map file"):
        resolve_world(str(tmp_path / "copy.sdf"))
