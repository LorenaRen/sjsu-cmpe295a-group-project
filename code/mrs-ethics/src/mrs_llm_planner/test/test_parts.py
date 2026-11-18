from mrs_llm_planner.parts import fold, merge, part_key, part_of, part_summary
from mrs_llm_planner.prompts import TranscriptEntry as E
import json

from plans import A_GOES, B_AFTER_A, B_NOW, TAKE_TURNS, part

TEAM = ("robot_a", "robot_b")
J = json.dumps


def test_part_of():
    assert part_of(J(TAKE_TURNS), "robot_b") == B_AFTER_A
    assert part_of(J(part(A_GOES)), "robot_b") is None
    assert part_of("junk", "robot_a") is None


def test_fold_versions_and_acceptance():
    entries = [E("coordinator", "prompt", "Mission"), E("robot_a", "proposal", "go", J(part(A_GOES)))]
    v = fold(entries, TEAM)
    assert v.change == 1 and v.accepted == ["robot_a"] and v.missing(TEAM) == ["robot_b"] and not v.agreed(TEAM)
    entries.append(E("robot_b", "proposal", "after you", J(TAKE_TURNS)))
    v = fold(entries, TEAM)
    assert v.change == 2 and v.accepted == ["robot_b"]
    entries.append(E("robot_a", "agreement", "ok"))
    v = fold(entries, TEAM)
    assert v.agreed(TEAM)
    plan, problem = v.team_plan(TEAM)
    assert problem == "" and [a.robot_id for a in plan.assignments] == TEAM_LIST
    assert plan.rationale == "robot_a: go robot_b: after you"


TEAM_LIST = ["robot_a", "robot_b"]


def test_unchanged_part_accepts():
    entries = [E("robot_a", "proposal", "", J(part(A_GOES))), E("robot_b", "proposal", "", J(TAKE_TURNS)),
               E("robot_a", "proposal", "again", J(TAKE_TURNS))]
    v = fold(entries, TEAM)
    assert v.change == 2 and set(v.accepted) == set(TEAM)


def test_invalid_team_plan_is_never_agreed():
    clash = {"robot_id": "robot_b", "tasks": [dict(B_NOW["tasks"][0], task_id="a1")]}
    entries = [E("robot_a", "proposal", "", J(part(A_GOES))),
               E("robot_b", "proposal", "", J({"assignments": [A_GOES, clash]})),
               E("robot_a", "agreement", "")]
    v = fold(entries, TEAM)
    plan, problem = v.team_plan(TEAM)
    assert plan is None and "duplicate task_id" in problem and not v.agreed(TEAM)


def test_summaries_and_keys():
    assert part_summary(B_AFTER_A) == "b1 navigate room_w (starts after a1)"
    assert part_summary(None) == "(no part yet)"
    reworded = dict(B_AFTER_A, tasks=[dict(B_AFTER_A["tasks"][0], justification="other", priority=7)])
    assert part_key(reworded) == part_key(B_AFTER_A) != part_key(B_NOW)
    assert merge({"robot_b": B_NOW, "robot_a": A_GOES}, TEAM)["assignments"] == [A_GOES, B_NOW]
