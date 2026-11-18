import json

import pytest

from mrs_llm_planner.prompts import TranscriptEntry, TurnRequest, turn_message
from mrs_llm_planner.routes import Graph, conflict_lines, node_zones, positions_from_text, route_lines
from plans import A_GOES, B_AFTER_A, B_NOW, part

TEAM = ("robot_a", "robot_b")
STARTS = {"robot_a": "room_w_s", "robot_b": "room_e_n"}


def p(robot, *tasks):
    return {"robot_id": robot, "tasks": [{"task_id": t, "action": "navigate", "target": n, "priority": 1,
                                          "justification": "", "depends_on": list(d)} for t, n, d in tasks]}


@pytest.fixture
def graph(world_map):
    return Graph(world_map.to_prompt_dict(), node_zones(world_map))


def test_shortest_path(graph):
    assert graph.path("room_w_s", "room_e_s") == ["room_w_s", "room_w", "aisle_w", "aisle_mid", "aisle_e",
                                                  "room_e", "room_e_s"]
    assert graph.path("room_w_s", "room_w_s") == ["room_w_s"]
    assert graph.path("room_w_s", "kitchen") == []
    assert graph.zones_on(graph.path("room_w_s", "room_e_s")) == ["room_w", "aisle", "room_e"]
    assert graph.limited("aisle") == 1 and graph.limited("room_w") is None


def test_route_lines(graph):
    lines = route_lines(graph, {"robot_a": A_GOES, "robot_b": B_AFTER_A}, STARTS, TEAM)
    assert lines[0] == ("  robot_a a1: room_w_s -> room_w -> aisle_w -> aisle_mid -> aisle_e -> room_e"
                        "   crosses aisle (narrow, capacity 1)")
    assert lines[1].startswith("  robot_b b1: room_e_n -> room_e -> aisle_e")


@pytest.mark.parametrize("a_goal, b_goal", [("room_e_s", "room_w_n"), ("room_e_n", "room_w_n")])
def test_the_failed_runs_show_a_conflict(graph, a_goal, b_goal):
    """llm_log cycles 1-3: each robot straight to its goal, no depends_on, rejected by S-1."""
    parts = {"robot_a": p("robot_a", ("a1", a_goal, ())), "robot_b": p("robot_b", ("b1", b_goal, ()))}
    [c] = conflict_lines(graph, parts, STARTS, TEAM)
    assert c.startswith("  CONFLICT: robot_a's a1 and robot_b's b1 both cross aisle (capacity 1) at the same time.")


def test_order_resolves_the_conflict(graph):
    assert conflict_lines(graph, {"robot_a": A_GOES, "robot_b": B_AFTER_A}, STARTS, TEAM) == []
    # a later task of robot_b, after one that depends on a1, is ordered too (tasks run in sequence)
    b = p("robot_b", ("b1", "room_e", ("a1",)), ("b2", "room_w", ()))
    assert conflict_lines(graph, {"robot_a": A_GOES, "robot_b": b}, STARTS, TEAM) == []
    # one robot staying on its side: no conflict
    stays = p("robot_b", ("b1", "room_e_s", ()))
    assert conflict_lines(graph, {"robot_a": A_GOES, "robot_b": stays}, STARTS, TEAM) == []


def test_positions_from_the_opening_prompt():
    text = "Mission: m\nRobots now: robot_a at aisle_mid; robot_b at aisle_e->room_e (start position).\nAgree"
    assert positions_from_text(text) == {"robot_a": "aisle_mid", "robot_b": "room_e"}
    assert positions_from_text("Robots now (start positions): robot_a at room_w_s.") == {"robot_a": "room_w_s"}


def test_turn_message_routes_and_hints(context):
    req = TurnRequest(round=1, transcript=(
        TranscriptEntry("coordinator", "prompt", "Mission: swap\nRobots now (start positions): robot_a at room_w_s; robot_b at room_e_n."),
        TranscriptEntry("robot_a", "proposal", "", json.dumps(part(A_GOES))),
        TranscriptEntry("robot_b", "proposal", "", json.dumps({"assignments": [A_GOES, B_NOW]})),
    ))
    text = turn_message(req, context)
    assert "Routes of the current team plan (each task drives the shortest path):" in text
    assert "CONFLICT: robot_a's a1 and robot_b's b1 both cross aisle" in text
    from dataclasses import replace
    text = turn_message(req, replace(context, conflict_hints=False))
    assert "Routes of the current team plan" in text and "CONFLICT" not in text and "No two robots" not in text


def test_no_conflict_message(context):
    req = TurnRequest(round=2, transcript=(
        TranscriptEntry("robot_a", "proposal", "", json.dumps(part(A_GOES))),
        TranscriptEntry("robot_b", "proposal", "", json.dumps({"assignments": [A_GOES, B_AFTER_A]})),
    ))
    assert "No two robots cross the same capacity-1 zone without an order." in turn_message(req, context)


def test_a_wait_task_does_not_hold_the_robot_back(graph):
    """Run 9: robot_a's 'a1 wait room_e_n, depends_on b1' still drives through the aisle at once
    (the gate times a wait like that and rejected the plan), so it is a conflict."""
    wait = {"robot_id": "robot_a", "tasks": [{"task_id": "a1", "action": "wait", "target": "room_e_n",
                                              "priority": 1, "justification": "", "depends_on": ["b1"]}]}
    b = p("robot_b", ("b1", "room_w_n", ()))
    [c] = conflict_lines(graph, {"robot_a": wait, "robot_b": b}, STARTS, TEAM)
    assert "robot_a's a1 and robot_b's b1 both cross aisle" in c and "A wait task does not help" in c
    # the same dependency on a navigate task does hold robot_a back
    nav = p("robot_a", ("a1", "room_e_n", ("b1",)))
    assert conflict_lines(graph, {"robot_a": nav, "robot_b": b}, STARTS, TEAM) == []
    # a wait at home first, then the drive: ordered
    home_then_go = {"robot_id": "robot_a", "tasks": [
        {"task_id": "a1", "action": "wait", "target": "room_w_s", "priority": 1, "justification": "",
         "depends_on": ["b1"]},
        {"task_id": "a2", "action": "navigate", "target": "room_e_n", "priority": 1, "justification": "",
         "depends_on": []}]}
    assert conflict_lines(graph, {"robot_a": home_then_go, "robot_b": b}, STARTS, TEAM) == []


def test_task_phrases_say_when_the_robot_drives():
    from mrs_llm_planner.parts import task_phrase
    t = {"task_id": "a1", "action": "wait", "target": "room_e_n", "depends_on": ["b1"]}
    assert task_phrase(t) == "a1 wait room_e_n (drives there now, then waits for b1)"
    assert task_phrase(dict(t, action="navigate")) == "a1 navigate room_e_n (starts after b1)"
    assert task_phrase(dict(t, depends_on=[])) == "a1 wait room_e_n"
