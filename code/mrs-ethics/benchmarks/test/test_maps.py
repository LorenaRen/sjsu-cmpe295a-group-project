"""The benchmark's maps: the two small ones as they are, and the generated large one."""
from collections import Counter

import pytest
import yaml

from bench import config
from bench.maps import bench_grid_map, halves, load_maps, stoppable_nodes

from fleet_checker.plan import RegionKind


@pytest.fixture(scope="module")
def maps():
    return load_maps()


def test_the_committed_large_map_is_what_the_generator_makes():
    assert yaml.safe_load(config.MAP_FILES["bench_grid"].read_text()) == bench_grid_map()


def test_the_large_map_has_rooms_joined_by_one_robot_corridors(maps):
    grid = maps["bench_grid"]
    assert len(grid.positions) == 128
    kinds = Counter(r.kind for r in grid.world.regions.values())
    assert kinds == {RegionKind.OPEN_AREA: 16, RegionKind.AISLE: 24}
    assert {r.capacity for r in grid.world.regions.values() if r.kind == RegionKind.AISLE} == {1}


def test_a_robot_may_stop_at_every_node_of_the_large_map(maps):
    grid = maps["bench_grid"]
    assert stoppable_nodes(grid) == sorted(grid.positions)
    west, east = halves(grid, stoppable_nodes(grid))
    assert (len(west), len(east)) == (64, 64)


def test_the_small_maps_have_six_nodes_a_robot_may_stop_at(maps):
    # The others are in a one-robot aisle or passage, a doorway or the restricted office.
    aisle, samples = maps["narrow_aisle"], maps["samples_world"]
    assert halves(aisle, stoppable_nodes(aisle)) == (["room_w", "room_w_n", "room_w_s"],
                                                      ["room_e", "room_e_n", "room_e_s"])
    assert halves(samples, stoppable_nodes(samples)) == (["room_2", "west_s"],
                                                          ["corridor_1", "corridor_2", "east_n", "room_3"])


def test_each_map_runs_only_as_many_robots_as_it_has_room_for(maps):
    # A start and a target each, apart from other robots' starts and targets where it matters.
    for name, counts in config.ROBOTS.items():
        assert max(counts) <= len(stoppable_nodes(maps[name])), name
    assert config.ROBOTS == {"narrow_aisle": (2, 4), "samples_world": (2, 4), "bench_grid": (2, 4, 8, 16, 32)}
