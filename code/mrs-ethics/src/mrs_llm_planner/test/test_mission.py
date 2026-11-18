import json

import pytest

from mrs_common.plan_schema import plan_from_json
from mrs_llm_planner.mission import (Goal, Mission, MissionError, check_goals, goals_from_text, parse_mission,
                                     room_nodes)
from plans import HEAD_ON, TAKE_TURNS

TEAM = ("robot_a", "robot_b")


@pytest.fixture
def nodes(world_map):
    return tuple(world_map.nodes)


@pytest.fixture
def rooms(world_map):
    return room_nodes(world_map)


def test_room_nodes(rooms):
    assert rooms == {"room_w": ("room_w", "room_w_n", "room_w_s"), "room_e": ("room_e", "room_e_n", "room_e_s")}


def test_line_form(nodes, rooms):
    m = parse_mission("robot_a: go to room_e. Robot_B: go to the room_w_n. (run 2)", TEAM, nodes, rooms)
    assert m.goals == {"robot_a": Goal("room_e", ("room_e", "room_e_n", "room_e_s")),
                       "robot_b": Goal("room_w_n", ("room_w_n",))}
    assert m.text.startswith("robot_a: go to room_e.")
    assert m.goals_line() == "Goals: robot_a -> room_e (end at room_e, room_e_n or room_e_s); robot_b -> room_w_n."


def test_without_rooms_a_goal_is_a_node(nodes):
    m = parse_mission("robot_a: go to room_e.", TEAM, nodes)
    assert m.goals == {"robot_a": Goal("room_e", ("room_e",))}


def test_json_form(nodes, rooms):
    m = parse_mission(json.dumps({"goals": {"robot_b": "room_w"}}), TEAM, nodes, rooms)
    assert m.goals["robot_b"].nodes == ("room_w", "room_w_n", "room_w_s") and m.text == "robot_b: go to room_w."
    assert parse_mission(json.dumps({"text": "swap", "goals": {"robot_a": "room_e"}}), TEAM, nodes, rooms).text == "swap"


def test_free_text_has_no_goals(nodes, rooms):
    assert parse_mission("patrol the west room", TEAM, nodes, rooms) == Mission("patrol the west room", {})


@pytest.mark.parametrize("raw, words", [
    ("robot_z: go to room_e.", "not in the team"),
    ("robot_a: go to kitchen.", "neither a room nor a node"),
    ("robot_a: go to aisle.", "neither a room nor a node"),          # a zone, but not a room
    ("robot_a: go to room_e. robot_a: go to room_w.", "two goals"),
    ('{"goals": ["room_e"]}', "must be"),
    ("{not json", "not valid JSON"),
    ("   ", "empty"),
])
def test_refused(nodes, rooms, raw, words):
    with pytest.raises(MissionError, match=words):
        parse_mission(raw, TEAM, nodes, rooms)


def test_goals_round_trip_through_the_opening_prompt(nodes, rooms):
    m = parse_mission("robot_a: go to room_e. robot_b: go to room_w_n.", TEAM, nodes, rooms)
    opening = f"Mission: {m.text}\n{m.goals_line()}\nRobots now (start positions): robot_a at room_w_s."
    assert goals_from_text(opening) == m.goals
    assert goals_from_text("Mission: patrol") == {}


def test_check_goals(rooms):
    take_turns = plan_from_json(json.dumps(TAKE_TURNS))      # robot_a ends at room_e, robot_b at room_w
    west, east = Goal("room_w", rooms["room_w"]), Goal("room_e", rooms["room_e"])
    assert check_goals(take_turns, {"robot_a": east, "robot_b": west}) == []
    assert check_goals(take_turns, {}) == []
    assert check_goals(take_turns, {"robot_a": Goal("room_e_s", ("room_e_s",))}) == [
        "robot_a's last task ends at room_e, but its mission goal is room_e_s"]
    assert check_goals(take_turns, {"robot_a": west}) == [
        "robot_a's last task ends at room_e, but its mission goal is room_w (end at room_w, room_w_n or room_w_s)"]
    one = plan_from_json(json.dumps({**HEAD_ON, "assignments": HEAD_ON["assignments"][:1]}))
    assert check_goals(one, {"robot_b": west})[0].startswith("robot_b is missing from the plan: a proposal must give every robot")


def test_the_failed_run_now_passes(rooms):
    """llm_log call 41: robot_b ended at room_w_s for the goal 'room_w'."""
    plan = json.loads(json.dumps(TAKE_TURNS))
    plan["assignments"][1]["tasks"][0]["target"] = "room_w_s"
    assert check_goals(plan_from_json(json.dumps(plan)), {"robot_b": Goal("room_w", rooms["room_w"])}) == []
