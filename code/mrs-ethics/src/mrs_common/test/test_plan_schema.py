"""TeamPlan / Constraint schema tests. Pure Python: run with plain `pytest`, no ROS needed."""
import json

import pytest
from pydantic import ValidationError

from mrs_common import load_sample
from mrs_common.plan_schema import (Action, Assignment, Constraint, Task, TeamPlan, check_references,
                                    constraints_from_json, constraints_to_json, hold_all_plan, plan_from_json,
                                    plan_to_json, team_plan_json_schema)


def make_plan(**overrides) -> TeamPlan:
    data = {
        "assignments": [
            {"robot_id": "robot_a", "tasks": [
                {"task_id": "a1", "action": "navigate", "target": "corridor_1", "priority": 1}]},
            {"robot_id": "robot_b", "tasks": [
                {"task_id": "b1", "action": "wait", "target": "corridor_2", "priority": 2, "depends_on": ["a1"]}]},
        ]
    }
    data.update(overrides)
    return TeamPlan.model_validate(data)


# ---- valid plans ---------------------------------------------------------------
@pytest.mark.parametrize("name", ["valid_two_robots.json", "exit_blocking.json"])
def test_valid_samples_parse(name):
    plan = plan_from_json(load_sample(name))
    assert len(plan.assignments) == 2


def test_json_round_trip_is_lossless():
    plan = plan_from_json(load_sample("valid_two_robots.json"))
    again = plan_from_json(plan_to_json(plan))
    assert again == plan
    assert again.model_dump() == plan.model_dump()


def test_unicode_and_quotes_survive_round_trip():
    plan = make_plan(rationale='Robot "A" goes first — patient in room 3 ✓')
    assert plan_from_json(plan_to_json(plan)).rationale == plan.rationale


def test_defaults_and_helpers():
    plan = make_plan()
    assert plan.schema_version == "1.0"
    assert plan.robot_ids == ["robot_a", "robot_b"]
    assert plan.robot_of("b1") == "robot_b"
    assert plan.task("a1").action is Action.NAVIGATE
    assert plan.tasks_of("robot_c") == []


# ---- invalid plans ---------------------------------------------------------------
@pytest.mark.parametrize("name, fragment", [
    ("invalid_duplicate_task.json", "duplicate task_id"),
    ("invalid_unknown_field.json", "Extra inputs are not permitted"),
    ("invalid_bad_action.json", "action"),
])
def test_invalid_samples_rejected(name, fragment):
    with pytest.raises(ValidationError) as exc:
        plan_from_json(load_sample(name))
    assert fragment in str(exc.value)


def test_robot_assigned_twice_rejected():
    with pytest.raises(ValidationError, match="more than once"):
        TeamPlan(assignments=[Assignment(robot_id="robot_a"), Assignment(robot_id="robot_a")])


def test_empty_plan_rejected():
    with pytest.raises(ValidationError):
        TeamPlan(assignments=[])


@pytest.mark.parametrize("field, value", [("priority", 0), ("priority", 11), ("target", ""),
                                          ("task_id", "has space")])
def test_task_field_bounds(field, value):
    data = {"task_id": "t1", "action": "navigate", "target": "room_1", "priority": 1}
    data[field] = value
    with pytest.raises(ValidationError):
        Task.model_validate(data)


def test_unknown_dependency_rejected():
    with pytest.raises(ValidationError, match="unknown task"):
        make_plan(assignments=[{"robot_id": "robot_a", "tasks": [
            {"task_id": "a1", "action": "navigate", "target": "x", "priority": 1, "depends_on": ["zz"]}]}])


def test_dependency_cycle_rejected():
    with pytest.raises(ValidationError, match="circular"):
        make_plan(assignments=[
            {"robot_id": "robot_a", "tasks": [
                {"task_id": "a1", "action": "navigate", "target": "x", "priority": 1, "depends_on": ["b1"]}]},
            {"robot_id": "robot_b", "tasks": [
                {"task_id": "b1", "action": "navigate", "target": "y", "priority": 1, "depends_on": ["a1"]}]},
        ])


def test_llm_style_garbage_rejected():
    with pytest.raises(ValidationError):
        plan_from_json('{"plan": "robot_a goes to room 3"}')
    with pytest.raises(ValidationError):
        plan_from_json("Sure! Here is the plan: ...")


# ---- grounding, fallback, constraints, schema export --------------------------------
def test_check_references():
    plan = plan_from_json(load_sample("valid_two_robots.json"))
    nodes = ["corridor_1", "corridor_2", "room_3", "triage"]
    assert check_references(plan, ["robot_a", "robot_b"], nodes) == []
    problems = check_references(plan, ["robot_a"], ["corridor_1"])
    assert "unknown robot 'robot_b'" in problems
    assert any("unknown target 'room_3'" in p for p in problems)


def test_hold_all_plan():
    plan = hold_all_plan(["robot_a", "robot_b"], {"robot_a": "corridor_1", "robot_b": "room_2"}, "attempts exhausted")
    assert all(t.action is Action.HOLD_POSITION for a in plan.assignments for t in a.tasks)
    assert plan.task("hold_robot_b").target == "room_2"


def test_constraints_round_trip():
    cs = [Constraint(kind="replace_target", rule_id="S1_exit_clearance", robot_id="robot_b", task_id="b1",
                     value="corridor_2", text="Park at corridor_2 instead of exit_A."),
          Constraint(kind="forbid_node", rule_id="S1_exit_clearance", node="exit_A", text="Never stop at exit_A.")]
    text = constraints_to_json(cs)
    assert json.loads(text)[1] == {"kind": "forbid_node", "rule_id": "S1_exit_clearance", "node": "exit_A",
                                   "text": "Never stop at exit_A."}
    assert constraints_from_json(text) == cs
    assert constraints_from_json("") == []


def test_constraint_needs_text_and_known_kind():
    with pytest.raises(ValidationError):
        Constraint(kind="forbid_node", rule_id="S1", node="exit_A", text="")
    with pytest.raises(ValidationError):
        Constraint.model_validate({"kind": "teleport", "rule_id": "X", "text": "no"})


def test_avoid_edge_constraint():
    c = Constraint(kind="avoid_edge", rule_id="blocked_route", value="corridor_1--room_3",
                   text="Corridor between corridor_1 and room_3 is blocked; route around it.")
    assert constraints_from_json(constraints_to_json([c])) == [c]
    for bad in (None, "room_3--corridor_1", "corridor_1->room_3", "room_3"):
        with pytest.raises(ValidationError):
            Constraint(kind="avoid_edge", rule_id="blocked_route", value=bad, text="blocked")


def test_json_schema_export():
    schema = team_plan_json_schema()
    assert "assignments" in schema["properties"]
    actions = schema["$defs"]["Action"]["enum"]
    assert set(actions) == {"navigate", "wait", "hold_position", "assist", "carry"}
