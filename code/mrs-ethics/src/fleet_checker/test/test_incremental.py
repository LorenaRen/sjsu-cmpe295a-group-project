"""Rechecks: TeamCheckResult.recheck may reuse what a change of depends_on cannot change, and must
give exactly what recheck_full, the check done again from scratch, gives. conftest.py compares the
two on every recheck of every test; these tests make sure that comparison covers the cases."""
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common.plan_schema import TeamPlan

from fleet_checker.adapters.compiler import OnEdge
from fleet_checker.adapters.sim_map import load_sim_map, sim_map_from_dict, spawn_starts
from fleet_checker.adapters.team_check import check_team_plan
from fleet_checker.adapters.verdict import to_verdict
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation

from conftest import CROSSROADS, assert_same_result

SAMPLES_WORLD = Path(__file__).parent / "fixtures" / "samples_world.yaml"
SETTINGS = {"speed": 0.5, "robot_length": 0.45}


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


def task(task_id, action, target, depends_on=()):
    return {"task_id": task_id, "action": action, "target": target, "priority": 1, "depends_on": list(depends_on)}


def team(*assignments) -> TeamPlan:
    return TeamPlan.model_validate({"assignments": [{"robot_id": r, "tasks": ts} for r, ts in assignments]})


def with_dependency(plan: TeamPlan, before: str, after: str) -> TeamPlan:
    data = plan.model_dump(mode="json")
    for a in data["assignments"]:
        for t in a["tasks"]:
            if t["task_id"] == after:
                t["depends_on"].append(before)
    return TeamPlan.model_validate(data)


@pytest.fixture(scope="module")
def no_order():
    data = plan_from_json(load_sample("valid_two_robots.json")).model_dump(mode="json")
    for a in data["assignments"]:
        for t in a["tasks"]:
            t["depends_on"] = []
    return TeamPlan.model_validate(data)


def test_a_verdicts_rechecks_are_compared_with_full_ones(samples_map, no_order, shadow_rechecks):
    result = check_team_plan(no_order, samples_map, spawn_starts(samples_map, ["robot_a", "robot_b"]), **SETTINGS)
    assert result.findings
    to_verdict(result)
    assert shadow_rechecks["rechecks"] >= 1


@pytest.mark.parametrize("before, after", [("a1", "b2"), ("a2", "b1"), ("b1", "a1"), ("b2", "a2")])
def test_a_dependency_added_anywhere(samples_map, no_order, before, after):
    result = check_team_plan(no_order, samples_map, spawn_starts(samples_map, ["robot_a", "robot_b"]), **SETTINGS)
    plan = with_dependency(no_order, before, after)
    assert_same_result(result.recheck(plan), result.recheck_full(plan))


def test_a_dependency_that_closes_a_cycle_through_task_order(samples_map):
    # b2 waits for a2, and a1 now waits for b2: a1 -> b2 -> a2 -> (robot_a's order) a1.
    plan = team(("robot_a", [task("a1", "navigate", "corridor_1"), task("a2", "navigate", "room_3")]),
                ("robot_b", [task("b1", "navigate", "corridor_2"), task("b2", "navigate", "room_2", ["a2"])]))
    result = check_team_plan(plan, samples_map, spawn_starts(samples_map, ["robot_a", "robot_b"]), **SETTINGS)
    again = result.recheck(with_dependency(plan, "b2", "a1"))
    assert [v.code for v in again.compiled.violations] == ["DEPENDENCY_DEADLOCK"]


def test_another_plan_altogether(samples_map, no_order):
    result = check_team_plan(no_order, samples_map, spawn_starts(samples_map, ["robot_a", "robot_b"]), **SETTINGS)
    other = team(("robot_a", [task("a1", "navigate", "room_3")]), ("robot_b", [task("b1", "navigate", "room_2")]))
    assert_same_result(result.recheck(other), result.recheck_full(other))


def test_a_robot_on_an_edge_with_blocked_edges(samples_map, no_order):
    starts = {"robot_a": "west_s", "robot_b": "pass_e"}
    result = check_team_plan(no_order, samples_map, starts, on_edges={"robot_b": OnEdge("corridor_1", "pass_e", (8.6, 0.0))},
                             blocked_edges=[("office_e", "office_1")], **SETTINGS)
    plan = with_dependency(no_order, "a1", "b1")
    assert_same_result(result.recheck(plan), result.recheck_full(plan))


def test_people_and_s2():
    crossroads = sim_map_from_dict(CROSSROADS)
    plan = team(("robot_a", [task("a1", "navigate", "e")]), ("robot_b", [task("b1", "navigate", "s")]))
    result = check_team_plan(plan, crossroads, {"robot_a": "w", "robot_b": "n"}, people=[
        HumanObservation(human_id="person_1", position=(5.0, 1.0))], crowding=CrowdingLimit(1.5, 1),
        speed=1.0, robot_length=0.45)
    assert result.findings
    for before, after in (("b1", "a1"), ("a1", "b1")):
        p = with_dependency(plan, before, after)
        assert_same_result(result.recheck(p), result.recheck_full(p))


# ---------------- what a recheck reuses: routes and robots whose timeline did not change ----------------

@pytest.fixture
def dijkstra_calls(monkeypatch):
    import fleet_checker.adapters.compiler as compiler
    calls = {"n": 0}
    real = compiler._shortest_path

    def counted(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(compiler, "_shortest_path", counted)
    return calls


def test_a_recheck_finds_no_route_again(samples_map, no_order, dijkstra_calls):
    result = check_team_plan(no_order, samples_map, spawn_starts(samples_map, ["robot_a", "robot_b"]), **SETTINGS)
    dijkstra_calls["n"] = 0
    TeamCheckResult_recheck(result, with_dependency(no_order, "a1", "b2"))
    assert dijkstra_calls["n"] == 0          # a dependency changes no route


def test_a_recheck_rebuilds_only_the_robots_whose_timeline_changed(samples_map, no_order):
    result = check_team_plan(no_order, samples_map, spawn_starts(samples_map, ["robot_a", "robot_b"]), **SETTINGS)
    again = TeamCheckResult_recheck(result, with_dependency(no_order, "a1", "b2"))    # b2 now waits for a1
    assert again.compiled.plans["robot_a"] is result.compiled.plans["robot_a"]
    assert again.compiled.plans["robot_b"] is not result.compiled.plans["robot_b"]
    # b1 is done long before a2 starts: waiting for it changes nothing, so nothing is rebuilt.
    same = TeamCheckResult_recheck(result, with_dependency(no_order, "b1", "a2"))
    assert result.compiled.timings["b1"].done < result.compiled.timings["a2"].depart
    assert all(same.compiled.plans[r] is result.compiled.plans[r] for r in ("robot_a", "robot_b"))


def test_a_recheck_of_another_plan_compiles_it_in_full(samples_map, no_order, dijkstra_calls):
    result = check_team_plan(no_order, samples_map, spawn_starts(samples_map, ["robot_a", "robot_b"]), **SETTINGS)
    dijkstra_calls["n"] = 0
    other = team(("robot_a", [task("a1", "navigate", "room_3")]), ("robot_b", [task("b1", "navigate", "room_2")]))
    TeamCheckResult_recheck(result, other)
    assert dijkstra_calls["n"] == 2


def TeamCheckResult_recheck(result, plan):
    """The recheck under test itself, not conftest's comparing wrapper (which also runs recheck_full)."""
    from conftest import INCREMENTAL_RECHECK
    return INCREMENTAL_RECHECK(result, plan)
