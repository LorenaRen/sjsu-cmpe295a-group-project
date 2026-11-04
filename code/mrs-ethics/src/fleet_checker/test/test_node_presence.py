"""node_presence: how a robot is at a node in its compiled plan (S-4).

Times are worked out from fixtures/samples_world.yaml at 0.5 m/s, as in test_compiler.py.
"""
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common.plan_schema import TeamPlan

from fleet_checker.adapters.compiler import OnEdge, compile_team_plan
from fleet_checker.adapters.node_presence import only_the_keeper_can_resolve, presence
from fleet_checker.adapters.sim_map import load_sim_map, spawn_starts

SAMPLES_WORLD = Path(__file__).parent / "fixtures" / "samples_world.yaml"


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


def task(task_id, action, target, depends_on=()):
    return {"task_id": task_id, "action": action, "target": target, "priority": 1,
            "depends_on": list(depends_on)}


def team(*assignments) -> TeamPlan:
    return TeamPlan.model_validate({"assignments": [{"robot_id": r, "tasks": ts} for r, ts in assignments]})


def at(sim_map, team_plan, robot, step, node):
    compiled = compile_team_plan(team_plan, sim_map, spawn_starts(sim_map, ["robot_a", "robot_b"]), speed=0.5)
    return presence(compiled, team_plan, robot, step, node)


VALID = plan_from_json(load_sample("valid_two_robots.json"))
# robot_a: west_hall [0, 6.4), passage [6.4, 18.4), east_hall to corridor_1 (a1) [18.4, 20.4),
#          east_hall to room_3 (a2) [20.4, 26.4), where it holds.
# robot_b: one east_hall step east_n -> room_3 -> corridor_1 -> corridor_2 (b1) [0, 11.4), waits at
#          corridor_2 for a1 [11.4, 20.4), then b2 to room_2 through the passage.


def test_driving_through_on_the_way(samples_map):
    p = at(samples_map, VALID, "robot_b", 0, "room_3")
    assert (p.passes, p.arrive_step, p.leave_step, p.arrive_task, p.leave_task) == (True, 0, 0, "b1", "b1")
    assert not p.from_start and not p.stays and not p.ends_task


def test_driving_through_where_one_task_ends_and_the_next_begins(samples_map):
    # a1 ends at corridor_1 and a2 drives on at once: robot_a does not stop there.
    p = at(samples_map, VALID, "robot_a", 2, "corridor_1")
    assert (p.passes, p.arrive_task, p.leave_task, p.ends_task) == (True, "a1", "a2", False)


def test_driving_off_from_the_start_node(samples_map):
    p = at(samples_map, VALID, "robot_b", 0, "east_n")
    assert (p.passes, p.from_start, p.leave_step, p.leave_task) == (True, True, 0, "b1")
    assert not p.on_lead_in


# robot_b is on the edge room_3 -> corridor_1, 1 m past room_3 (team_state's at "room_3->corridor_1"):
# its plan starts with one east_hall step that reaches corridor_1 at 4 s.
ON_EDGE = {"robot_b": OnEdge("room_3", "corridor_1", (11.0, 0.0))}
STARTS = {"robot_a": "west_s", "robot_b": "corridor_1"}


def at_end_of_edge(sim_map, team_plan, step, node="corridor_1"):
    compiled = compile_team_plan(team_plan, sim_map, STARTS, speed=0.5, on_edges=ON_EDGE)
    return presence(compiled, team_plan, "robot_b", step, node)


def test_reaching_a_node_on_the_edge_it_is_on(samples_map):
    p = at_end_of_edge(samples_map, team(("robot_b", [task("b1", "navigate", "room_2")])), 0)
    # Brought there by no task, and not there when its plan starts: it can neither come later nor leave first.
    assert (p.passes, p.arrive_step, p.arrive_task, p.leave_task, p.on_lead_in) == (True, 0, None, "b1", True)
    assert not p.from_start


def test_waiting_where_the_edge_it_is_on_ends(samples_map):
    plan = team(("robot_a", [task("a1", "navigate", "pass_w")]),          # done at 7.6 s
                ("robot_b", [task("b1", "navigate", "room_2", depends_on=["a1"])]))
    p = at_end_of_edge(samples_map, plan, 1)   # step 1 stands at corridor_1 from 4 s until a1 is done
    assert (p.passes, p.arrive_step, p.arrive_task, p.task, p.on_lead_in) == (False, 0, None, "b1", True)


def test_a_robot_without_tasks_stays_where_its_edge_ends(samples_map):
    p = at_end_of_edge(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])), 0)
    assert (p.passes, p.stays, p.task, p.ends_task, p.on_lead_in) == (False, True, None, False, True)


def test_standing_where_a_task_ends_until_the_next_one_leaves(samples_map):
    p = at(samples_map, VALID, "robot_b", 1, "corridor_2")      # b1, a wait task, waits for a1
    assert (p.passes, p.arrive_task, p.leave_task, p.task, p.ends_task) == (False, "b1", "b2", "b1", True)


def test_the_drive_to_a_node_where_the_robot_then_stands_is_that_stop(samples_map):
    driving_there = at(samples_map, VALID, "robot_b", 0, "corridor_2")
    assert driving_there == at(samples_map, VALID, "robot_b", 1, "corridor_2")


def test_staying_to_the_end_of_the_plan(samples_map):
    p = at(samples_map, VALID, "robot_a", 3, "room_3")
    assert (p.passes, p.stays, p.arrive_task, p.leave_task, p.ends_task) == (False, True, "a2", None, True)


def test_waiting_at_the_start_node_before_the_first_task(samples_map):
    plan = team(("robot_a", [task("a1", "navigate", "corridor_1")]),
                ("robot_b", [task("b1", "navigate", "room_3", depends_on=["a1"])]))
    p = at(samples_map, plan, "robot_b", 0, "east_n")
    assert (p.passes, p.from_start, p.leave_task, p.task, p.ends_task) == (False, True, "b1", "b1", False)


def test_waiting_between_tasks_where_the_next_one_does_not_go(samples_map):
    # b2 waits at corridor_1, where b1 ended, for a1: c is not where b2 goes.
    plan = team(("robot_a", [task("a1", "navigate", "corridor_1")]),
                ("robot_b", [task("b1", "navigate", "corridor_1"),
                             task("b2", "navigate", "room_3", depends_on=["a1"])]))
    p = at(samples_map, plan, "robot_b", 1, "corridor_1")
    assert (p.passes, p.arrive_task, p.leave_task, p.task, p.ends_task) == (False, "b1", "b2", "b2", False)


def test_a_robot_without_tasks_stands_at_its_start_to_the_end(samples_map):
    p = at(samples_map, team(("robot_a", [task("a1", "navigate", "room_2")])), "robot_b", 0, "east_n")
    assert (p.from_start, p.stays, p.task, p.ends_task) == (True, True, None, False)


# ---------------- who can resolve an S-4 conflict ----------------

def roles(sim_map, team_plan, yielder, y_step, keeper, k_step, node):
    return at(sim_map, team_plan, yielder, y_step, node), at(sim_map, team_plan, keeper, k_step, node)


def test_only_the_keeper_can_free_a_node_it_never_leaves_where_the_other_robots_task_ends(samples_map):
    both = team(("robot_a", [task("a1", "hold_position", "room_3")]),
                ("robot_b", [task("b1", "hold_position", "room_3")]))
    y, k = roles(samples_map, both, "robot_a", 2, "robot_b", 0, "room_3")
    assert (y.ends_task, k.stays) == (True, True)
    assert only_the_keeper_can_resolve(y, k)


def test_only_the_keeper_can_free_a_node_it_never_leaves_that_the_other_robot_drives_through(samples_map):
    plan = team(("robot_a", [task("a1", "hold_position", "room_3")]),
                ("robot_b", [task("b1", "hold_position", "corridor_1")]))
    y, k = roles(samples_map, plan, "robot_a", 2, "robot_b", 0, "corridor_1")
    assert (y.passes, k.stays) == (True, True)
    assert only_the_keeper_can_resolve(y, k)


def test_waiting_somewhere_else_is_up_to_the_yielder(samples_map):
    # robot_b only waits at corridor_1 between b1 and b2, where robot_a ends: it can wait elsewhere.
    plan = team(("robot_a", [task("a1", "hold_position", "corridor_1")]),
                ("robot_b", [task("b1", "navigate", "corridor_1"),
                             task("b2", "navigate", "room_3", depends_on=["a1"])]))
    y, k = roles(samples_map, plan, "robot_b", 1, "robot_a", 2, "corridor_1")
    assert (y.ends_task, y.passes, k.stays) == (False, False, True)
    assert not only_the_keeper_can_resolve(y, k)


def test_a_keeper_that_leaves_can_be_waited_for(samples_map):
    # robot_b waits at corridor_2 until robot_a has got there, then leaves with b2.
    plan = team(("robot_a", [task("a1", "hold_position", "corridor_2")]),
                ("robot_b", [task("b1", "navigate", "corridor_2"),
                             task("b2", "navigate", "room_3", depends_on=["a1"])]))
    y, k = roles(samples_map, plan, "robot_a", 2, "robot_b", 1, "corridor_2")
    assert (y.ends_task, k.stays, k.leave_task) == (True, False, "b2")
    assert not only_the_keeper_can_resolve(y, k)


def test_a_yielder_there_from_its_start_can_leave_first(samples_map):
    plan = team(("robot_a", [task("a1", "hold_position", "east_n")]))
    y, k = roles(samples_map, plan, "robot_b", 0, "robot_a", 2, "east_n")
    assert (y.from_start, k.stays) == (True, True)
    assert not only_the_keeper_can_resolve(y, k)
