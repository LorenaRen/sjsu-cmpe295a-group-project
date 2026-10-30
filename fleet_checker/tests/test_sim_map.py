"""mrs_sim map adapter: zones become regions, edges are cut at zone boundaries."""
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from checker.adapters.sim_map import (
    NO_LIMIT, MapError, load_sim_map, sim_map_from_dict, spawn_starts,
)
from checker.plan import RegionKind

FIXTURES = Path(__file__).parent / "fixtures"
NARROW_AISLE = FIXTURES / "narrow_aisle.yaml"
SAMPLES_WORLD = FIXTURES / "samples_world.yaml"
# Shervan's original; only in the repo once mrs_sim is merged (now on origin/module2-simpackage).
REPO_NARROW_AISLE = (Path(__file__).resolve().parents[2]
                     / "code/mrs-ethics/src/mrs_sim/worlds/narrow_aisle.yaml")


@pytest.fixture(scope="module")
def aisle_map():
    return load_sim_map(NARROW_AISLE)


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


def pieces(sim_map, a, b):
    return [(p.zone, round(p.length, 9)) for p in sim_map.pieces[(a, b)]]


# ---------------- narrow_aisle (copy of Shervan's world) ----------------

class TestNarrowAisle:

    def test_zones_become_regions(self, aisle_map):
        regions = aisle_map.world.regions
        assert set(regions) == {"room_w", "aisle", "room_e"}
        assert regions["aisle"].kind == RegionKind.AISLE
        assert regions["aisle"].capacity == 1
        assert regions["room_w"].kind == RegionKind.OPEN_AREA
        assert regions["room_w"].capacity == NO_LIMIT   # capacity not given = no limit

    def test_adjacency_comes_from_the_graph(self, aisle_map):
        regions = aisle_map.world.regions
        assert regions["room_w"].neighbors == {"aisle"}
        assert regions["aisle"].neighbors == {"room_w", "room_e"}
        assert regions["room_e"].neighbors == {"aisle"}

    def test_edge_is_cut_where_it_crosses_into_the_aisle(self, aisle_map):
        # room_w (x=0) -> aisle_w (x=2.6); the aisle zone starts at x=2.0.
        assert pieces(aisle_map, "room_w", "aisle_w") == [("room_w", 2.0), ("aisle", 0.6)]
        assert pieces(aisle_map, "aisle_w", "room_w") == [("aisle", 0.6), ("room_w", 2.0)]
        assert pieces(aisle_map, "aisle_w", "aisle_mid") == [("aisle", 3.4)]

    def test_each_node_is_in_one_zone(self, aisle_map):
        assert aisle_map.zone_of["aisle_mid"] == "aisle"
        assert aisle_map.zone_of["room_w_s"] == "room_w"
        assert aisle_map.zone_of["room_e_n"] == "room_e"

    def test_spawn_slots_in_order(self, aisle_map):
        assert spawn_starts(aisle_map, ["robot_a", "robot_b"]) == {
            "robot_a": "room_w_s", "robot_b": "room_e_n"}
        with pytest.raises(ValueError, match="spawn slots"):
            spawn_starts(aisle_map, ["r1", "r2", "r3", "r4", "r5"])

    @pytest.mark.skipif(not REPO_NARROW_AISLE.exists(),
                        reason="mrs_sim is not on this branch yet (origin/module2-simpackage)")
    def test_fixture_matches_mrs_sim(self):
        """Drift check: the copy must equal Shervan's file once that file is in the repo."""
        assert yaml.safe_load(NARROW_AISLE.read_text()) == yaml.safe_load(REPO_NARROW_AISLE.read_text())


# ---------------- samples_world (test map for the Module 1 samples) ----------------

class TestSamplesWorld:

    def test_zone_types_map_to_region_kinds(self, samples_map):
        regions = samples_map.world.regions
        assert regions["passage"].kind == RegionKind.AISLE
        assert regions["door_a"].kind == RegionKind.KEEP_CLEAR
        assert regions["door_a"].capacity == 1
        assert regions["office"].kind == RegionKind.RESTRICTED
        assert regions["west_hall"].kind == RegionKind.OPEN_AREA

    def test_edge_through_a_zone_with_no_node(self, samples_map):
        # office_w (y=4) -> room_2 (y=0) passes door_b (y in [2, 3]), which has no node.
        assert "door_b" not in samples_map.zone_of.values()
        assert pieces(samples_map, "office_w", "room_2") == [
            ("office", 1.0), ("door_b", 1.0), ("west_hall", 2.0)]
        assert samples_map.world.regions["door_b"].neighbors == {"office", "west_hall"}

    def test_halls_are_not_adjacent_to_each_other(self, samples_map):
        regions = samples_map.world.regions
        assert regions["west_hall"].neighbors == {"passage", "door_b"}
        assert regions["east_hall"].neighbors == {"passage", "door_a"}


# ---------------- rejected maps ----------------

def tiny(**patch) -> dict:
    """Two nodes on one edge that crosses from a room into a narrow zone at x=2."""
    data = {
        "world": "tiny",
        "nodes": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 4.0, "y": 0.0}},
        "edges": [["a", "b"]],
        "zones": [
            {"id": "left", "type": "room", "rect": {"x_min": -1, "x_max": 2, "y_min": -1, "y_max": 1}},
            {"id": "right", "type": "narrow", "capacity": 1,
             "rect": {"x_min": 2, "x_max": 5, "y_min": -1, "y_max": 1}},
        ],
        "spawns": [{"node": "a"}],
    }
    data.update(patch)
    return data


def test_tiny_map_loads():
    sm = sim_map_from_dict(tiny())
    assert pieces(sm, "a", "b") == [("left", 2.0), ("right", 2.0)]


@pytest.mark.parametrize("patch, match", [
    ({"zones": [{"id": "left", "type": "room", "rect": {"x_min": -1, "x_max": 3, "y_min": -1, "y_max": 1}},
                {"id": "right", "type": "room", "rect": {"x_min": 2, "x_max": 5, "y_min": -1, "y_max": 1}}]},
     "overlap"),
    ({"zones": [{"id": "left", "type": "room", "rect": {"x_min": -1, "x_max": 1.5, "y_min": -1, "y_max": 1}},
                {"id": "right", "type": "room", "rect": {"x_min": 2, "x_max": 5, "y_min": -1, "y_max": 1}}]},
     "leaves every zone"),
    ({"nodes": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 2.0, "y": 0.0}}}, "boundary"),
    ({"nodes": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 4.0, "y": 0.0}, "c": {"x": 9.0, "y": 9.0}}},
     "outside every zone"),
    ({"zones": [{"id": "left", "type": "corridor", "rect": {"x_min": -1, "x_max": 5, "y_min": -1, "y_max": 1}}]},
     "unknown zone type"),
    ({"zones": [{"id": "z", "type": "room", "rect": {"x_min": -1, "x_max": 2, "y_min": -1, "y_max": 1}},
                {"id": "z", "type": "room", "rect": {"x_min": 2, "x_max": 5, "y_min": -1, "y_max": 1}}]},
     "duplicate zone ids"),
    ({"edges": [["a", "zz"]]}, "unknown node"),
    ({"edges": [["a", "b"], ["b", "a"]]}, "duplicate edge"),
    ({"nodes": {"a": {"x": 0.0, "y": 0.0}, "b": {"x": 4.0, "y": 0.0}, "c": {"x": 0.0, "y": 0.0}},
      "edges": [["a", "b"], ["a", "c"]]}, "zero length"),
    ({"spawns": [{"node": "zz"}]}, "spawn"),
])
def test_rejected_maps(patch, match):
    with pytest.raises(MapError, match=match):
        sim_map_from_dict(tiny(**patch))


def test_unknown_field_is_a_schema_error():
    with pytest.raises(ValidationError):
        sim_map_from_dict(tiny(blocked_edges=[]))
