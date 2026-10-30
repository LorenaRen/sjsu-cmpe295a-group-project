"""Plan compiler: TeamPlan (Module 1) + mrs_sim map -> one timed Plan per robot.

Expected times are worked out by hand from the coordinates in fixtures/samples_world.yaml.
"""
from pathlib import Path

import pytest
from mrs_common import hold_all_plan, load_sample, plan_from_json
from mrs_common.plan_schema import TeamPlan

from checker.adapters.compiler import PARKED_S, StepOrigin, compile_team_plan
from checker.adapters.sim_map import load_sim_map, sim_map_from_dict, spawn_starts
from checker.plan import validate_plan

SAMPLES_WORLD = Path(__file__).parent / "fixtures" / "samples_world.yaml"
SPEED = 0.5  # m/s; a test value, not Module 6's speed (mrs_sim clamps cmd_vel at 0.5)


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


def sample(name: str) -> TeamPlan:
    return plan_from_json(load_sample(name))


def task(task_id, action, target, depends_on=(), priority=1) -> dict:
    return {"task_id": task_id, "action": action, "target": target,
            "priority": priority, "depends_on": list(depends_on)}


def team(*assignments) -> TeamPlan:
    return TeamPlan.model_validate(
        {"assignments": [{"robot_id": r, "tasks": tasks} for r, tasks in assignments]})


def compile_on(sim_map, plan, robots=("robot_a", "robot_b"), **kwargs):
    return compile_team_plan(plan, sim_map, spawn_starts(sim_map, list(robots)), speed=SPEED, **kwargs)


def spans(plan):
    return [(s.action.value, s.region, round(s.t_start, 6), round(s.t_end, 6)) for s in plan.steps]


def regions(plan):
    return [s.region for s in plan.steps]


def codes(violations):
    return [v.code for v in violations]


# ---------------- timeline ----------------

class TestTimeline:

    def test_route_is_split_where_it_changes_zone(self, samples_map):
        c = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "corridor_1")])),
                       robots=("robot_a",))
        # west_s -> room_2 -> pass_w -> pass_e -> corridor_1:
        # 3.2 m in west_hall, 6.0 m in the passage, 1.0 m in east_hall.
        assert spans(c.plans["robot_a"]) == [
            ("navigate", "west_hall", 0.0, 6.4),
            ("navigate", "passage", 6.4, 18.4),
            ("navigate", "east_hall", 18.4, 20.4),
        ]
        assert [o.nodes for o in c.origins["robot_a"]] == [
            ("room_2",), ("pass_w", "pass_e"), ("corridor_1",)]

    def test_wait_drives_first_then_waits_for_its_dependency(self, samples_map):
        c = compile_on(samples_map, sample("valid_two_robots.json"))
        a1, b1, b2 = c.timings["a1"], c.timings["b1"], c.timings["b2"]
        assert b1.depart == 0.0                            # does not wait to leave
        assert b1.arrive == pytest.approx(11.4)            # east_n -> corridor_2 is 5.7 m
        assert b1.done == a1.done == pytest.approx(20.4)   # then waits there for a1
        assert b2.depart == b1.done
        assert spans(c.plans["robot_b"])[1] == ("wait", "east_hall", 11.4, 20.4)

    def test_navigate_with_dependency_waits_where_it_is(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "corridor_1")]),
                    ("robot_b", [task("b1", "navigate", "room_2", depends_on=["a1"])]))
        c = compile_on(samples_map, plan)
        assert spans(c.plans["robot_b"])[0] == ("wait", "east_hall", 0.0, 20.4)
        assert c.timings["b1"].depart == pytest.approx(20.4)

    def test_task_at_the_current_node_takes_no_time(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "west_s"), task("a2", "navigate", "room_2")]))
        c = compile_on(samples_map, plan, robots=("robot_a",))
        assert c.timings["a1"] == (0.0, 0.0, 0.0)
        assert spans(c.plans["robot_a"]) == [("navigate", "west_hall", 0.0, 2.4)]


# ---------------- robots that do not move ----------------

class TestParkedRobots:

    def test_hold_all_fallback_parks_every_robot(self, samples_map):
        plan = hold_all_plan(["robot_a", "robot_b"], {"robot_a": "west_s", "robot_b": "east_n"},
                             reason="planning_exhausted")
        c = compile_on(samples_map, plan)
        for robot, zone in (("robot_a", "west_hall"), ("robot_b", "east_hall")):
            assert spans(c.plans[robot]) == [("wait", zone, 0.0, PARKED_S)]
            assert c.origins[robot][0].task_id == f"hold_{robot}"

    def test_robot_without_tasks_still_occupies_its_zone(self, samples_map):
        c = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])))
        # robot_b is in the world but the plan gives it nothing to do.
        assert spans(c.plans["robot_b"]) == [("wait", "east_hall", 0.0, PARKED_S)]
        assert c.origins["robot_b"] == (StepOrigin(None, ("east_n",), None),)


# ---------------- routing ----------------

class TestRouting:

    def test_only_a_task_target_sends_a_robot_through_the_office(self, samples_map):
        c = compile_on(samples_map, sample("restricted_target.json"))
        # a1 targets office_1 (in through door_b), a2 goes on to room_3 (out through door_a).
        assert regions(c.plans["robot_a"]) == [
            "west_hall", "door_b", "office", "office", "door_a", "east_hall"]
        assert regions(c.plans["robot_b"]) == ["east_hall", "passage", "west_hall"]

    def test_equal_length_routes_are_decided_by_node_names(self):
        # s -> x -> t and s -> y -> t are both 2 m; "x" sorts before "y".
        square = sim_map_from_dict({
            "world": "square",
            "nodes": {"s": {"x": 0, "y": 0}, "y": {"x": 1, "y": 0},
                      "x": {"x": 0, "y": 1}, "t": {"x": 1, "y": 1}},
            "edges": [["s", "y"], ["y", "t"], ["s", "x"], ["x", "t"]],
            "zones": [{"id": "z", "type": "room",
                       "rect": {"x_min": -1, "x_max": 2, "y_min": -1, "y_max": 2}}],
            "spawns": [{"node": "s"}],
        })
        c = compile_team_plan(team(("robot_a", [task("a1", "navigate", "t")])), square,
                              {"robot_a": "s"}, speed=1.0)
        assert c.origins["robot_a"][0].nodes == ("x", "t")


# ---------------- compile errors ----------------

class TestCompileErrors:

    def test_every_unknown_target_is_reported_at_once(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "room_9"), task("a2", "navigate", "lobby")]))
        c = compile_on(samples_map, plan, robots=("robot_a",))
        assert codes(c.violations) == ["UNKNOWN_TARGET", "UNKNOWN_TARGET"]
        assert c.plans == {}

    def test_unknown_robot(self, samples_map):
        c = compile_on(samples_map, team(("robot_z", [task("z1", "navigate", "room_2")])))
        assert codes(c.violations) == ["UNKNOWN_ROBOT"]

    def test_no_route(self):
        islands = sim_map_from_dict({
            "world": "islands",
            "nodes": {"a": {"x": 0, "y": 0}, "b": {"x": 1, "y": 0},
                      "c": {"x": 3, "y": 0}, "d": {"x": 4, "y": 0}},
            "edges": [["a", "b"], ["c", "d"]],
            "zones": [{"id": "z", "type": "room",
                       "rect": {"x_min": -1, "x_max": 5, "y_min": -1, "y_max": 1}}],
            "spawns": [{"node": "a"}],
        })
        c = compile_team_plan(team(("robot_a", [task("a1", "navigate", "d")])), islands,
                              {"robot_a": "a"}, speed=1.0)
        assert codes(c.violations) == ["NO_ROUTE"]

    def test_deadlock_through_task_order(self, samples_map):
        """depends_on alone is acyclic, so Module 1 accepts this plan (team() would
        raise otherwise); with each robot's task order added, it is a cycle."""
        plan = team(
            ("robot_a", [task("a1", "navigate", "room_2", depends_on=["b2"]),
                         task("a2", "navigate", "west_s")]),
            ("robot_b", [task("b1", "navigate", "room_3", depends_on=["a2"]),
                         task("b2", "navigate", "east_n")]))
        c = compile_on(samples_map, plan)
        assert codes(c.violations) == ["DEPENDENCY_DEADLOCK"]
        assert c.violations[0].message.endswith(
            "a1 depends on b2; b2 comes after b1 on robot_b; "
            "b1 depends on a2; a2 comes after a1 on robot_a")

    def test_bad_arguments_raise(self, samples_map):
        plan = sample("valid_two_robots.json")
        with pytest.raises(ValueError, match="speed"):
            compile_team_plan(plan, samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0)
        with pytest.raises(ValueError, match="not on map"):
            compile_team_plan(plan, samples_map, {"robot_a": "nowhere", "robot_b": "east_n"},
                              speed=SPEED)


# ---------------- invariants ----------------

@pytest.mark.parametrize("name", ["valid_two_robots.json", "keep_clear_stop.json",
                                  "restricted_target.json"])
def test_compiled_plans_pass_validate_plan(samples_map, name):
    c = compile_on(samples_map, sample(name))
    for plan in c.plans.values():
        assert validate_plan(plan, samples_map.world, c.snapshot) == []


def test_priority_is_carried_for_logs(samples_map):
    c = compile_on(samples_map, sample("valid_two_robots.json"))
    assert {o.priority for o in c.origins["robot_a"]} == {1}
    assert {o.priority for o in c.origins["robot_b"]} == {2}


def test_plan_ids_follow_the_transaction(samples_map):
    c = compile_on(samples_map, sample("valid_two_robots.json"), plan_id="tx-1", revision=2)
    assert c.plans["robot_b"].plan_id == "tx-1/robot_b"
    assert c.plans["robot_b"].revision == 2
