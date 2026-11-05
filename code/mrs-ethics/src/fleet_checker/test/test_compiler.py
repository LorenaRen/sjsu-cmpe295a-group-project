"""Plan compiler: TeamPlan (Module 1) + mrs_sim map -> one timed Plan per robot.

Expected times are worked out by hand from the coordinates in fixtures/samples_world.yaml.
"""
import math
from itertools import pairwise
from pathlib import Path

import pytest
from mrs_common import hold_all_plan, load_sample, plan_from_json
from mrs_common.plan_schema import TeamPlan

from fleet_checker.adapters.compiler import PARKED_S, OnEdge, StepOrigin, compile_team_plan, waits_for_task
from fleet_checker.adapters.sim_map import load_sim_map, sim_map_from_dict, spawn_starts
from fleet_checker.plan import validate_plan

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


def paths(plan):
    return [tuple((round(x, 6), round(y, 6)) for x, y in s.path) for s in plan.steps]


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


# ---------------- paths (where the robot is during each step) ----------------

class TestPaths:
    """PlanStep.path: what S-2 measures the distance to a person from."""

    def test_route_points_include_where_it_crosses_a_zone_boundary(self, samples_map):
        c = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "corridor_1")])),
                       robots=("robot_a",))
        # west_s -> room_2 -> pass_w -> pass_e -> corridor_1; the passage runs from x = 2 to x = 8.
        assert paths(c.plans["robot_a"]) == [
            ((0.0, -1.2), (0.0, 0.0), (2.0, 0.0)),
            ((2.0, 0.0), (2.6, 0.0), (7.4, 0.0), (8.0, 0.0)),
            ((8.0, 0.0), (9.0, 0.0)),
        ]

    def test_every_step_is_driven_at_the_compile_speed(self, samples_map):
        c = compile_on(samples_map, sample("restricted_target.json"))
        for plan in c.plans.values():
            for s in plan.steps:
                if len(s.path) > 1:
                    length = sum(math.dist(p, q) for p, q in pairwise(s.path))
                    assert length / (s.t_end - s.t_start) == pytest.approx(SPEED)

    def test_a_wait_task_stands_at_its_target(self, samples_map):
        c = compile_on(samples_map, sample("valid_two_robots.json"))
        wait = c.plans["robot_b"].steps[1]   # at corridor_2, waiting for a1
        assert (wait.action.value, wait.path) == ("wait", ((9.0, -1.5),))

    def test_a_navigate_waiting_for_a_dependency_stands_where_it_is(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "corridor_1")]),
                    ("robot_b", [task("b1", "navigate", "room_2", depends_on=["a1"])]))
        c = compile_on(samples_map, plan)
        assert c.plans["robot_b"].steps[0].path == ((12.0, 1.2),)   # east_n

    def test_a_parked_robot_stands_at_its_start(self, samples_map):
        c = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])))
        assert paths(c.plans["robot_b"]) == [((12.0, 1.2),)]


# ---------------- end_node (where each step ends, for S-4) ----------------

def end_nodes(plan):
    return [s.end_node for s in plan.steps]


class TestEndNodes:

    def test_a_step_ending_at_a_zone_boundary_has_no_end_node(self, samples_map):
        c = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "corridor_1")])),
                       robots=("robot_a",))
        # west_hall ends at x = 2 and the passage at x = 8, both boundaries; the west_hall step
        # passes room_2 on the way, which does not make room_2 its end.
        assert end_nodes(c.plans["robot_a"]) == [None, None, "corridor_1"]
        assert c.origins["robot_a"][0].nodes == ("room_2",)

    def test_standing_steps_end_where_they_stand(self, samples_map):
        c = compile_on(samples_map, sample("valid_two_robots.json"))
        assert end_nodes(c.plans["robot_b"])[:2] == ["corridor_2", "corridor_2"]   # drives there, waits
        plan = team(("robot_a", [task("a1", "navigate", "corridor_1")]),
                    ("robot_b", [task("b1", "navigate", "room_2", depends_on=["a1"])]))
        assert compile_on(samples_map, plan).plans["robot_b"].steps[0].end_node == "east_n"

    def test_a_parked_robot_ends_at_its_start(self, samples_map):
        c = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])))
        assert end_nodes(c.plans["robot_b"]) == ["east_n"]


# ---------------- waypoints (every node a step reaches, and when; for S-4) ----------------

def waypoints(plan):
    return [[(w.node, round(w.t, 6)) for w in s.waypoints] for s in plan.steps]


class TestWaypoints:

    def test_the_nodes_a_step_reaches_on_the_way_are_waypoints_too(self, samples_map):
        # east_n -> room_3 -> corridor_1 is all in east_hall, so it is one step: room_3 is reached
        # on the way (1.2 m, 2.4 s), not at a step boundary. The first step starts at east_n.
        c = compile_on(samples_map, team(("robot_b", [task("b1", "hold_position", "corridor_1")])))
        assert waypoints(c.plans["robot_b"]) == [[("east_n", 0.0), ("room_3", 2.4), ("corridor_1", 8.4)]]

    def test_each_step_lists_its_waypoints_at_the_compile_speed(self, samples_map):
        c = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "corridor_1")])),
                       robots=("robot_a",))
        # west_hall, passage, east_hall: the first and second steps end at zone boundaries.
        assert waypoints(c.plans["robot_a"]) == [[("west_s", 0.0), ("room_2", 2.4)],
                                                 [("pass_w", 7.6), ("pass_e", 17.2)],
                                                 [("corridor_1", 20.4)]]

    def test_a_robot_that_waits_at_its_start_first_has_no_start_waypoint(self, samples_map):
        # Its standing step is where it is at its start node; the drive after it starts there.
        plan = team(("robot_a", [task("a1", "navigate", "corridor_1")]),
                    ("robot_b", [task("b1", "navigate", "room_3", depends_on=["a1"])]))
        robot_b = compile_on(samples_map, plan).plans["robot_b"]
        assert [s.action.value for s in robot_b.steps] == ["wait", "navigate"]
        assert waypoints(robot_b) == [[], [("room_3", 22.8)]]

    def test_standing_steps_and_parked_robots_have_none(self, samples_map):
        c = compile_on(samples_map, sample("valid_two_robots.json"))
        robot_b = c.plans["robot_b"]
        assert [(s.action.value, len(s.waypoints)) for s in robot_b.steps][:2] == [("navigate", 4),
                                                                                    ("wait", 0)]
        parked = compile_on(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])))
        assert waypoints(parked.plans["robot_b"]) == [[]]

    @pytest.mark.parametrize("name", ["valid_two_robots.json", "keep_clear_stop.json", "restricted_target.json"])
    def test_waypoints_follow_the_nodes_each_step_reaches(self, samples_map, name):
        c = compile_on(samples_map, sample(name))
        for robot, plan in c.plans.items():
            for i, (step, origin) in enumerate(zip(plan.steps, c.origins[robot])):
                if step.action.value != "navigate":
                    continue
                nodes = [w.node for w in step.waypoints]
                if i == 0:   # the start node comes first
                    assert nodes[0] == spawn_starts(samples_map, ["robot_a", "robot_b"])[robot]
                    nodes = nodes[1:]
                assert nodes == list(origin.nodes), (robot, i)


# ---------------- a robot between two nodes (team_state's at "a->b") ----------------

# robot_b drives corridor_1 -> pass_e and is at (8.6, 0): a quarter of the 1.6 m edge is behind
# it. The rest is 0.6 m in east_hall (to the passage at x = 8) and 0.6 m in the passage.
ON_EDGE = OnEdge("corridor_1", "pass_e", (8.6, 0.0))
FLEET = {"robot_a": "west_s", "robot_b": "pass_e"}   # robot_b's plan starts where its edge ends


def compile_with_edge(sim_map, plan, on_edge=ON_EDGE, **kwargs):
    return compile_team_plan(plan, sim_map, FLEET, speed=SPEED, on_edges={"robot_b": on_edge}, **kwargs)


class TestOnEdge:

    def test_a_robot_on_an_edge_first_drives_the_rest_of_it(self, samples_map):
        c = compile_with_edge(samples_map, team(("robot_b", [task("b1", "navigate", "room_2")])))
        robot_b = c.plans["robot_b"]
        assert spans(robot_b) == [("navigate", "east_hall", 0.0, 1.2), ("navigate", "passage", 1.2, 2.4),
                                  ("navigate", "passage", 2.4, 13.2), ("navigate", "west_hall", 13.2, 17.2)]
        assert paths(robot_b)[:2] == [((8.6, 0.0), (8.0, 0.0)), ((8.0, 0.0), (7.4, 0.0))]
        # It is not at a node when the plan starts, so no start waypoint; it reaches pass_e at 2.4 s.
        assert waypoints(robot_b)[:3] == [[], [("pass_e", 2.4)], [("pass_w", 12.0)]]
        # The rest of the edge is part of no task; the compiler says how many steps it takes.
        assert [o.task_id for o in c.origins["robot_b"]] == [None, None, "b1", "b1"]
        assert c.lead_ins == {"robot_b": 2}
        assert c.snapshot.robot_regions["robot_b"] == "east_hall"

    def test_what_waits_for_its_first_task_waits_for_the_rest_of_the_edge_too(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "room_2", depends_on=["b1"])]),
                    ("robot_b", [task("b1", "navigate", "corridor_1")]))
        c = compile_with_edge(samples_map, plan)
        assert tuple(round(x, 6) for x in c.timings["b1"]) == (2.4, 5.6, 5.6)   # pass_e -> corridor_1
        assert round(c.timings["a1"].depart, 6) == 5.6

    def test_a_robot_on_an_edge_without_a_task_stops_where_the_edge_ends(self, samples_map):
        c = compile_with_edge(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])))
        assert spans(c.plans["robot_b"]) == [("navigate", "east_hall", 0.0, 1.2), ("navigate", "passage", 1.2, 2.4)]
        assert end_nodes(c.plans["robot_b"]) == [None, "pass_e"]   # where its plan ends: it stays there

    def test_the_position_counts_where_it_projects_onto_the_edge(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "room_2")]))
        beside = compile_with_edge(samples_map, plan, OnEdge("corridor_1", "pass_e", (8.6, 0.3)))
        assert spans(beside.plans["robot_b"]) == [("navigate", "east_hall", 0.0, 1.2),
                                                   ("navigate", "passage", 1.2, 2.4)]
        # Before the start of the edge: all of it is left.
        before = compile_with_edge(samples_map, plan, OnEdge("corridor_1", "pass_e", (9.5, 0.0)))
        assert spans(before.plans["robot_b"]) == [("navigate", "east_hall", 0.0, 2.0),
                                                   ("navigate", "passage", 2.0, 3.2)]

    def test_a_robot_at_the_end_of_its_edge_starts_there(self, samples_map):
        c = compile_with_edge(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])),
                              OnEdge("corridor_1", "pass_e", (7.0, 0.0)))   # past pass_e: nothing left
        assert spans(c.plans["robot_b"]) == [("wait", "passage", 0.0, PARKED_S)]
        assert c.lead_ins == {}

    def test_on_edges_must_agree_with_the_start_nodes_and_the_map(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "room_2")]))
        with pytest.raises(ValueError, match="robot_b is on corridor_1->pass_e but its plan would start at east_n"):
            compile_team_plan(plan, samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=SPEED,
                              on_edges={"robot_b": ON_EDGE})
        with pytest.raises(ValueError, match="corridor_1->room_2 is not an edge of map 'samples_world'"):
            compile_team_plan(plan, samples_map, {"robot_a": "west_s", "robot_b": "room_2"}, speed=SPEED,
                              on_edges={"robot_b": OnEdge("corridor_1", "room_2", (8.0, 0.0))})


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


# ---------------- blocked edges (team_state's blocked_edges) ----------------

PASSAGE = ("pass_w", "pass_e")


class TestBlockedEdges:

    def test_a_route_goes_around_a_blocked_edge(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "corridor_1")]))
        assert regions(compile_on(samples_map, plan, robots=("robot_a",)).plans["robot_a"]) == [
            "west_hall", "passage", "east_hall"]
        # With the passage blocked, the only way east is the bypass through the office:
        # west_s -> room_2 -> office_w -> office_1 -> office_e -> door_A -> corridor_1, 18.2 m.
        c = compile_on(samples_map, plan, robots=("robot_a",), blocked_edges=[PASSAGE])
        assert spans(c.plans["robot_a"]) == [
            ("navigate", "west_hall", 0.0, 6.4), ("navigate", "door_b", 6.4, 8.4),
            ("navigate", "office", 8.4, 30.4), ("navigate", "door_a", 30.4, 32.4),
            ("navigate", "east_hall", 32.4, 36.4)]

    def test_an_edge_is_blocked_both_ways(self, samples_map):
        plan = team(("robot_b", [task("b1", "navigate", "room_2")]))
        c = compile_on(samples_map, plan, blocked_edges=[PASSAGE])         # given west to east, driven east to west
        assert "passage" not in regions(c.plans["robot_b"])
        assert regions(c.plans["robot_b"]) == regions(
            compile_on(samples_map, plan, blocked_edges=[("pass_e", "pass_w")]).plans["robot_b"])

    def test_no_route_says_which_edges_are_blocked(self, samples_map):
        # room_3 is east_n's only way out.
        c = compile_on(samples_map, team(("robot_b", [task("b1", "navigate", "corridor_2")])),
                       blocked_edges=[("room_3", "corridor_1"), PASSAGE])
        assert [(v.code, v.message) for v in c.violations] == [
            ("NO_ROUTE", "task 'b1' (robot_b): no path from 'east_n' to 'corridor_2' without the blocked "
                         "edges corridor_1--room_3, pass_e--pass_w")]

    def test_blocked_edges_must_be_edges_of_the_map(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "room_2")]))
        with pytest.raises(ValueError, match="room_2--room_3 is not an edge of map 'samples_world'"):
            compile_on(samples_map, plan, blocked_edges=[("room_3", "room_2")])

    def test_a_robot_cannot_start_on_a_blocked_edge(self, samples_map):
        plan = team(("robot_a", [task("a1", "navigate", "room_2")]))
        with pytest.raises(ValueError, match="robot_b is on corridor_1->pass_e, which is blocked"):
            compile_with_edge(samples_map, plan, blocked_edges=[("pass_e", "corridor_1")])


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
        assert plan.has_paths   # and Plan itself checked that each step starts where the last ended


def test_priority_is_carried_for_logs(samples_map):
    c = compile_on(samples_map, sample("valid_two_robots.json"))
    assert {o.priority for o in c.origins["robot_a"]} == {1}
    assert {o.priority for o in c.origins["robot_b"]} == {2}


def test_plan_ids_follow_the_transaction(samples_map):
    c = compile_on(samples_map, sample("valid_two_robots.json"), plan_id="tx-1", revision=2)
    assert c.plans["robot_b"].plan_id == "tx-1/robot_b"
    assert c.plans["robot_b"].revision == 2


def test_a_task_waits_for_what_its_depends_on_and_its_robots_earlier_tasks_wait_for():
    plan = team(("robot_a", [task("a1", "navigate", "room_2"), task("a2", "navigate", "west_s")]),
                ("robot_b", [task("b1", "navigate", "room_3"), task("b2", "navigate", "east_n", depends_on=["a2"])]))
    assert waits_for_task(plan, "b2", "a2")       # depends_on
    assert waits_for_task(plan, "b2", "a1")       # a2 comes after a1 on robot_a
    assert waits_for_task(plan, "b2", "b1")       # b2 comes after b1 on robot_b
    assert not waits_for_task(plan, "a1", "b2") and not waits_for_task(plan, "b1", "a1")
    assert not waits_for_task(plan, "a1", "a1")
