"""Parsing a reply: the speaker's own part, merged into the current team plan, validated."""
import json

import pytest

from mrs_llm_planner.mission import Goal
from mrs_llm_planner.turns import ReplyError, extract_json, parse_reply
from plans import A_GOES, B_AFTER_A, B_NOW, HALLUCINATED, TAKE_TURNS, agreement, part, proposal

TEAM = ("robot_a", "robot_b")


@pytest.fixture
def nodes(world_map):
    return tuple(world_map.nodes)


def parse(raw, nodes, robot="robot_b", others=None, goals=None):
    return parse_reply(raw, robot_id=robot, team=TEAM, graph_nodes=nodes, others=others, goals=goals)


def test_part_is_merged_into_the_team_plan(nodes):
    r = parse(proposal(part(B_AFTER_A)), nodes, others={"robot_a": A_GOES})
    assert r.kind == "proposal" and r.part["robot_id"] == "robot_b"
    assert [a.robot_id for a in r.plan.assignments] == ["robot_a", "robot_b"]
    assert r.plan.assignments[1].tasks[0].depends_on == ["a1"]


def test_first_part_alone(nodes):
    r = parse(proposal(part(A_GOES)), nodes, robot="robot_a")
    assert [a.robot_id for a in r.plan.assignments] == ["robot_a"]


def test_teammates_assignments_are_ignored(nodes):
    """phi3.5 writes the whole team's plan: only the speaker's own part is used."""
    full = json.loads(json.dumps(TAKE_TURNS))
    full["assignments"][1]["tasks"][0]["target"] = "room_e_n"     # what robot_a 'wants' for robot_b
    r = parse(proposal(full), nodes, robot="robot_a", others={"robot_b": B_NOW})
    assert r.part == A_GOES
    assert r.plan.assignments[1].tasks[0].target == "room_w"     # robot_b's own part stands


def test_own_part_missing(nodes):
    with pytest.raises(ReplyError, match='must contain your own part: an assignment with "robot_id": "robot_b"'):
        parse(proposal(part(A_GOES)), nodes)


def test_dependency_on_a_task_nobody_has(nodes):
    with pytest.raises(ReplyError, match="depends on unknown task.*current team plan"):
        parse(proposal(part(B_AFTER_A)), nodes)                    # robot_a has no part yet


def test_task_id_clash_with_teammate(nodes):
    clash = {"robot_id": "robot_b", "tasks": [dict(B_NOW["tasks"][0], task_id="a1")]}
    with pytest.raises(ReplyError, match="duplicate task_id.*e.g. b1"):
        parse(proposal(part(clash)), nodes, others={"robot_a": A_GOES})


def test_repeated_own_assignments_are_joined(nodes):
    two = {"schema_version": "1.1", "assignments": [
        {"robot_id": "robot_a", "tasks": [A_GOES["tasks"][0]]},
        {"robot_id": "robot_a", "tasks": [dict(A_GOES["tasks"][0], task_id="a2", action="hold_position",
                                               depends_on=["a1"])]}]}
    r = parse(proposal(two), nodes, robot="robot_a")
    assert [t.task_id for t in r.plan.assignments[0].tasks] == ["a1", "a2"]


def test_own_goal_only(nodes):
    goals = {"robot_a": Goal("room_e_s", ("room_e_s",)), "robot_b": Goal("room_w", ("room_w",))}
    # robot_b's part meets robot_b's goal; robot_a's (not robot_b's business) misses its own
    r = parse(proposal(part(B_AFTER_A)), nodes, others={"robot_a": A_GOES}, goals=goals)
    assert r.kind == "proposal"
    with pytest.raises(ReplyError, match="robot_a's last task ends at room_e, but its mission goal is room_e_s"):
        parse(proposal(part(A_GOES)), nodes, robot="robot_a", goals=goals)


def test_grounding(nodes):
    with pytest.raises(ReplyError, match="unknown target 'kitchen'"):
        parse(proposal(HALLUCINATED), nodes, robot="robot_a")


def test_agreement_in_prose_and_fences_and_with_a_plan(nodes):
    r = parse("Sure!\n```json\n" + agreement("fine by me") + "\n```", nodes)
    assert r.kind == "agreement" and r.text == "fine by me" and r.plan is None
    r = parse(json.dumps({"kind": "agreement", "text": "ok", "plan": {"anything": 1}}), nodes)
    assert r.kind == "agreement" and r.plan is None


@pytest.mark.parametrize("raw, words", [
    ("no json here", "not JSON"),
    ("[1, 2]", "one JSON object"),
    ('{"kind": "proposal", "text": "x"}', "must include 'plan'"),
    ('{"kind": "vote"}', "kind"),
    ('{"kind": "discussion", "extra": 1}', "extra"),
])
def test_bad_replies(nodes, raw, words):
    with pytest.raises(ReplyError, match=words):
        parse(raw, nodes)


def test_extract_json_keeps_outermost():
    assert extract_json('x {"a": {"b": 1}} y') == {"a": {"b": 1}}


def test_reply_schema_shape(nodes):
    from mrs_llm_planner.turns import reply_schema
    s = reply_schema("robot_b", nodes, ["a1", "a2"])
    proposal_s, other_s = s["anyOf"]
    assert proposal_s["required"] == ["kind", "text", "plan"] and other_s["required"] == ["kind", "text"]
    task = proposal_s["properties"]["plan"]["properties"]["assignments"]["items"]["properties"]["tasks"]["items"]
    assert task["properties"]["task_id"]["pattern"] == "^b[0-9]+$"
    assert set(task["properties"]["target"]["enum"]) == set(nodes)
    assert task["required"] == ["task_id", "action", "target", "priority", "justification", "depends_on"]
    assert proposal_s["properties"]["plan"]["required"] == ["assignments", "rationale"]   # reasons are required
    assert reply_schema("robot_a", nodes)["anyOf"][0]["properties"]["plan"]["properties"]["assignments"][
        "items"]["properties"]["tasks"]["items"]["properties"]["depends_on"]["items"] == {"type": "string",
                                                                                         "pattern": "^a[0-9]+$"}


def test_schema_accepts_what_the_parser_accepts(nodes):
    """A reply that follows the schema is one parse_reply() can use (given goals and teammates fit)."""
    pytest.importorskip("jsonschema")
    import jsonschema
    from mrs_llm_planner.turns import reply_schema
    s = reply_schema("robot_b", nodes, ["a1"])
    jsonschema.validate(json.loads(proposal(part(B_AFTER_A))), s)
    jsonschema.validate(json.loads(agreement()), s)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(json.loads(proposal(part(A_GOES))), s)     # another robot's part
