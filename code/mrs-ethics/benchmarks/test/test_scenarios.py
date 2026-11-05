"""Benchmark scenarios: drawn from a seed string, the same on every run, and checked against the
committed manifest."""
import json
import math
import os
import subprocess
import sys

import pytest

from bench import config
from bench.maps import halves, load_maps, stoppable_nodes
from bench.scenarios import (cells, check_manifest, generate, manifest_rows, people_cells, scenarios_of)


@pytest.fixture(scope="module")
def maps():
    return load_maps()


def test_the_same_seed_gives_the_same_scenario(maps):
    a = generate(maps, "bench_grid", 16, "medium", 7)
    b = generate(maps, "bench_grid", 16, "medium", 7)
    assert a == b and a.digest() == b.digest()
    assert a.seed == f"{config.MASTER_SEED}/bench_grid/16/medium/7"
    assert generate(maps, "bench_grid", 16, "medium", 8).digest() != a.digest()


def test_a_scenario_is_a_fleet_at_its_own_nodes_each_sent_to_its_own_target(maps):
    s = generate(maps, "bench_grid", 32, "high", 3)
    pool = set(stoppable_nodes(maps["bench_grid"]))
    assert list(s.starts) == [f"robot_{k:02d}" for k in range(32)]
    assert len(set(s.starts.values())) == 32 and set(s.starts.values()) <= pool
    targets = {a.robot_id: [t.target for t in a.tasks] for a in s.plan.assignments}
    assert all(len(ts) == 1 for ts in targets.values())
    assert len({ts[0] for ts in targets.values()}) == 32 and {ts[0] for ts in targets.values()} <= pool
    assert all(targets[r][0] != s.starts[r] for r in s.starts)
    assert all(t.action.value == "hold_position" and not t.depends_on for a in s.plan.assignments for t in a.tasks)


def test_density_is_how_likely_a_robot_is_sent_to_the_other_half(maps):
    grid = maps["bench_grid"]
    west, _ = halves(grid, stoppable_nodes(grid))

    def crossings(s):
        side = {n: n in west for n in grid.positions}
        return sum(side[s.starts[a.robot_id]] != side[a.tasks[0].target] for a in s.plan.assignments)

    low, high = generate(maps, "bench_grid", 16, "low", 0), generate(maps, "bench_grid", 16, "high", 0)
    assert (crossings(low), low.crossings) == (0, 0)
    assert (crossings(high), high.crossings) == (16, 16)


def test_a_half_with_no_target_left_gives_way_to_the_other(maps):
    # samples_world's west half has two open-area nodes: four robots sent across cannot all cross.
    for i in range(20):
        s = generate(maps, "samples_world", 4, "high", i)
        assert 0 < s.crossings <= 4
    assert any(generate(maps, "samples_world", 4, "high", i).crossings < 4 for i in range(20))


def test_people_are_near_open_area_nodes_and_leave_the_plan_as_it_was(maps):
    plain, crowded = generate(maps, "bench_grid", 8, "medium", 5), generate(maps, "bench_grid", 8, "medium", 5, people=4)
    assert crowded.plan == plain.plan and crowded.starts == plain.starts and plain.people == ()
    assert [p.human_id for p in crowded.people] == ["person_0", "person_1", "person_2", "person_3"]
    grid = maps["bench_grid"]
    pool = stoppable_nodes(grid)
    for p in crowded.people:
        assert min(math.dist(p.position, grid.positions[n]) for n in pool) <= config.PERSON_OFFSET
        assert grid.zones_at(*p.position)


def test_the_cells_are_the_maps_fleet_sizes_and_densities(maps):
    assert len(cells()) == (2 + 2 + 5) * 3
    assert people_cells() == [("bench_grid", 8, "medium", 4), ("bench_grid", 16, "medium", 4)]
    assert len(scenarios_of(maps, ("narrow_aisle", 2, "low", 0), 3)) == 3


def test_the_generator_still_makes_the_committed_scenarios(maps):
    # The manifest has one digest per cell over all its scenarios: a change to the generator, the
    # seeds or the maps shows here, before any result is compared with an old one.
    assert check_manifest(maps, config.MANIFEST) == []
    assert len(manifest_rows(maps)) == len(cells()) + len(people_cells())


def test_the_scenarios_do_not_depend_on_the_hash_seed(maps):
    # Another process, another PYTHONHASHSEED: set and dict-of-str orders change, the scenarios may not.
    code = ("import json; from bench.maps import load_maps; from bench.scenarios import manifest_rows; "
            "print(json.dumps(manifest_rows(load_maps())))")
    out = subprocess.run([sys.executable, "-c", code], cwd=config.BENCH_DIR, capture_output=True, text=True,
                         env={**os.environ, "PYTHONHASHSEED": "12345"}, check=True)
    assert json.loads(out.stdout) == manifest_rows(maps)
