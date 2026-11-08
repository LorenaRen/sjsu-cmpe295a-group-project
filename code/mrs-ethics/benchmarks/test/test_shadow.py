"""The in-process side of the equivalence test: while the benchmark's scenarios and repair runs go,
every recheck must equal a recheck done in full (TeamCheckResult.recheck_full)."""
import pytest

from bench.maps import load_maps
from bench.perf import check_kwargs
from bench.repair import run_scenario
from bench.runner import QUICK
from bench.scenarios import cells, generate, people_cells, scenarios_of
from bench.shadow import shadow_rechecks

from fleet_checker.adapters.team_check import check_team_plan
from fleet_checker.adapters.verdict import to_verdict


@pytest.fixture(scope="module")
def maps():
    return load_maps()


def verdicts_and_repairs(maps, timing, repairs):
    with shadow_rechecks() as stats:
        for s in timing:
            to_verdict(check_team_plan(s.plan, maps[s.map_name], s.starts, **check_kwargs(s)))
        for s in repairs:
            run_scenario(s, maps[s.map_name], max_attempts=5)
    return stats


def test_every_recheck_of_the_quick_scenarios_and_their_repairs_equals_a_full_one(maps):
    timing = [s for c in cells(QUICK.robots) + people_cells(QUICK.people_robots)
              for s in scenarios_of(maps, c, QUICK.perf_scenarios)]
    repairs = [s for c in cells(QUICK.robots) for s in scenarios_of(maps, c, QUICK.repair_scenarios)]
    stats = verdicts_and_repairs(maps, timing, repairs)
    assert stats["rechecks"] > 0 and stats["mismatches"] == []


def test_every_recheck_of_a_16_robot_fleet_equals_a_full_one(maps):
    timing = [generate(maps, "bench_grid", 16, "high", i) for i in range(3)]
    stats = verdicts_and_repairs(maps, timing, timing[:2])
    assert stats["rechecks"] > 0 and stats["mismatches"] == []
