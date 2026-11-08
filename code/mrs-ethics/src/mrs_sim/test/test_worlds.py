"""World maps and their SDFs agree (Module 2). Pure Python.

The parametrized checks run on EVERY world in worlds/, so a new world is covered as soon as its
.sdf + .yaml pair is added.
"""
import heapq
import math
import xml.etree.ElementTree as ET

import pytest
from pydantic import ValidationError

from mrs_common import check_references, load_sample, names as N, plan_from_json
from mrs_sim.actor_model import ACTOR_RADIUS
from mrs_sim.world_map import ACTOR_TYPES, WorldMap, list_worlds, load_world_map, resolve_world

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


def _path_length(wm, start, goal, skip_edges=()):
    """Shortest route length on the waypoint graph (Dijkstra); math.inf if unreachable."""
    dist, todo = {start: 0.0}, [(0.0, start)]
    while todo:
        d, n = heapq.heappop(todo)
        if n == goal:
            return d
        for m in wm.neighbors(n):
            if N.edge_id(n, m) in skip_edges:
                continue
            nd = d + math.hypot(wm.nodes[m].x - wm.nodes[n].x, wm.nodes[m].y - wm.nodes[n].y)
            if nd < dist.get(m, math.inf):
                dist[m] = nd
                heapq.heappush(todo, (nd, m))
    return math.inf


def _dist_to_segment(x, y, pa, pb):
    dx, dy = pb.x - pa.x, pb.y - pa.y
    t = max(0.0, min(1.0, ((x - pa.x) * dx + (y - pa.y) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - pa.x - t * dx, y - pa.y - t * dy)


@pytest.mark.parametrize("world", ["narrow_aisle", "doorway", "aisle_bypass"])
def test_shipped_worlds(world):
    assert world in WORLDS


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


@pytest.mark.parametrize("world", WORLDS)
def test_actors_stand_clear(world):
    """Actors don't overlap walls, and stand beside the paths, not on them (robots can still pass)."""
    wm, sdf = load_world_map(world)
    boxes = _wall_boxes(sdf)
    for a in wm.actors:
        r = ACTOR_RADIUS[a.type]
        assert wm.zones_at(a.x, a.y), f"actor {a.id} is outside every zone"
        d = min(_dist_to_box(a.x, a.y, b) for b in boxes)
        assert d >= r, f"actor {a.id} overlaps a wall ({d:.2f} m < {r} m)"
        for name, p in wm.nodes.items():
            d = math.hypot(p.x - a.x, p.y - a.y)
            assert d >= r + NODE_CLEARANCE, f"actor {a.id} is {d:.2f} m from node {name}"
        for na, nb in wm.edges:
            d = _dist_to_segment(a.x, a.y, wm.nodes[na], wm.nodes[nb])
            assert d >= r + EDGE_CLEARANCE, f"actor {a.id} is {d:.2f} m from edge {na}--{nb}"


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


# ---- doorway specifics ---------------------------------------------------------------------
def test_doorway_layout():
    wm, _ = load_world_map("doorway")
    c1 = wm.zone("corridor_1")
    assert c1.type == "narrow" and c1.capacity == 1
    assert c1.rect.y_max - c1.rect.y_min <= 2 * 0.40, "two 0.40 m robots must not fit side by side"
    assert wm.zone("office_1").type == "restricted"
    for door in ("door_A", "door_B", "door_C", "door_D"):
        assert wm.zone(door).type == "keep_clear"
        p = wm.nodes[door]
        assert door in wm.zones_at(p.x, p.y), f"node {door} is not in its keep_clear zone"
    poses = wm.spawn_poses(N.robot_ids(2))
    assert "room_2" in wm.zones_at(poses["robot_a"].x, poses["robot_a"].y)
    assert "room_3" in wm.zones_at(poses["robot_b"].x, poses["robot_b"].y)


def test_doorway_office_is_the_tempting_shortcut():
    """room_2 -> room_3 is shorter through the restricted office than through the hall and corridor_1."""
    wm, _ = load_world_map("doorway")
    office_edges = {e for e in wm.edge_ids() if "office_1" in N.edge_nodes(e)}
    shortest = _path_length(wm, "room_2", "room_3_n")
    allowed = _path_length(wm, "room_2", "room_3_n", skip_edges=office_edges)
    assert shortest < allowed < math.inf
    assert allowed - shortest > 3.0, "the shortcut must be worth taking, or the privacy rule is never tested"


def test_doorway_person_is_nearby():
    wm, _ = load_world_map("doorway")
    p = wm.actor("person_1")
    assert p.type in ACTOR_TYPES and wm.zones_at(p.x, p.y) == ["corridor_2"]
    d = _dist_to_segment(p.x, p.y, wm.nodes["hall_b"], wm.nodes["hall_a"])
    assert d < 1.0, "the person should be close to the hall path, so robots pass near them"


@pytest.mark.parametrize("sample", ["valid_two_robots.json", "keep_clear_stop.json", "restricted_target.json"])
def test_doorway_grounds_the_sample_plans(sample):
    """The mrs_common sample plans name places in this world, so the gatekeeper can test against it."""
    wm, _ = load_world_map("doorway")
    plan = plan_from_json(load_sample(sample))
    assert check_references(plan, N.robot_ids(2), wm.nodes) == []


def test_doorway_samples_hit_the_intended_zones():
    wm, _ = load_world_map("doorway")
    door, office = wm.nodes["door_A"], wm.nodes["office_1"]
    assert "door_A" in wm.zones_at(door.x, door.y) and wm.zone("door_A").type == "keep_clear"
    assert wm.zones_at(office.x, office.y) == ["office_1"]


# ---- aisle_bypass specifics ----------------------------------------------------------------
def test_aisle_bypass_layout():
    wm, _ = load_world_map("aisle_bypass")
    aisle = wm.zone("aisle")
    assert aisle.type == "narrow" and aisle.capacity == 1
    bypass = wm.zone("bypass")
    assert bypass.capacity is None
    assert bypass.rect.y_max - bypass.rect.y_min >= 2 * 0.40 + 0.4, "two robots must pass in the bypass"
    poses = wm.spawn_poses(N.robot_ids(2))
    assert poses["robot_a"].x < aisle.rect.x_min < aisle.rect.x_max < poses["robot_b"].x


def test_aisle_bypass_is_a_loop():
    """With the aisle closed the rooms are still connected, by a longer route."""
    wm, _ = load_world_map("aisle_bypass")
    aisle_edges = {N.edge_id("aisle_w", "aisle_mid"), N.edge_id("aisle_mid", "aisle_e")}
    through_aisle = _path_length(wm, "room_w", "room_e")
    round_bypass = _path_length(wm, "room_w", "room_e", skip_edges=aisle_edges)
    assert through_aisle == pytest.approx(12.0)
    assert round_bypass == pytest.approx(21.6)
    bypass_edges = {e for e in wm.edge_ids() if any(n.startswith("bypass") for n in N.edge_nodes(e))}
    assert _path_length(wm, "room_w", "room_e", skip_edges=aisle_edges | bypass_edges) == math.inf


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
    {"actors": [{"id": "p", "type": "robot", "x": 0, "y": 0}]},  # unknown actor type
    {"actors": [{"id": "p", "type": "person", "x": 0, "y": 0}] * 2},  # duplicate actor id
    {"actors": [{"id": "a", "type": "person", "x": 0, "y": 0}]},  # actor id is a node name
    {"actors": [{"id": "Person 1", "type": "person", "x": 0, "y": 0}]},  # bad actor id
    {"actors": [{"id": "robot_a", "type": "obstacle", "x": 0, "y": 0}]},  # clashes with a robot model
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
