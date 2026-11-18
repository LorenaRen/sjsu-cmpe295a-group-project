import json

from mrs_llm_planner.guidelines import load_guidelines
from mrs_llm_planner.prompts import (EXAMPLE_REPLY, TranscriptEntry, TurnRequest, plan_summary, system_prompt,
                                     transcript_digest, turn_message)
from plans import A_GOES, B_AFTER_A, B_NOW, TAKE_TURNS, part

J = json.dumps


def test_system_prompt_has_map_rules_and_parts(context):
    text = system_prompt(context)
    assert "robot_a" in text and "robot_b" in text and '"aisle_mid"' in text
    assert load_guidelines()["rules"]["S-1"] in text
    assert load_guidelines()["rules"]["S-2"] not in text          # S-2 is off by default
    assert "You plan ONLY\nfor robot_a" in text
    assert '"plan" holds "assignments" with exactly one entry, for robot_a' in text
    assert 'start it with your robot\'s letter: "a1", "a2"' in text
    assert "only one chance" in text and "attempt" not in text
    example = text[text.index('{\n "kind": "proposal"'):text.index("Example of an agreement")]
    assert json.loads(example) == EXAMPLE_REPLY


def test_example_names_nothing_real(context):
    """phi3.5 copied an example built from the real team and map as its plan."""
    plan = EXAMPLE_REPLY["plan"]
    robots = {a["robot_id"] for a in plan["assignments"]}
    targets = {t["target"] for a in plan["assignments"] for t in a["tasks"]}
    assert len(plan["assignments"]) == 1                          # a part: one robot
    assert not robots & set(context.team) and not targets & set(context.map_dict["nodes"])


def test_turn_message_shows_parts_and_the_team_plan(context):
    req = TurnRequest(round=2, world_summary={"robot_id": "robot_a", "at": "room_w_s"}, transcript=(
        TranscriptEntry("coordinator", "prompt", "Mission: swap rooms"),
        TranscriptEntry("robot_a", "proposal", "I go first", J(part(A_GOES))),
        TranscriptEntry("robot_b", "proposal", "I follow", J(TAKE_TURNS)),
    ))
    text = turn_message(req, context)
    assert "Round 2 of 3" in text and "Mission: swap rooms" in text and '"at":"room_w_s"' in text
    assert ("- robot_a PROPOSES ITS PART (team plan version 1): I go first\n"
            "  tasks (what robot_a will actually do): a1 navigate room_e") in text
    assert ("- robot_b PROPOSES ITS PART (team plan version 2): I follow\n"
            "  tasks (what robot_b will actually do): b1 navigate room_w (starts after a1)") in text
    assert ("What each robot will actually do (only tasks count, not words): "
            "robot_a: a1 navigate room_e; robot_b: b1 navigate room_w (starts after a1).") in text
    assert "Current team plan (version 2): {" in text and '"depends_on":["a1"]' in text
    assert "Accepted so far by: robot_b; still needed: robot_a." in text
    assert "Your current part: a1 navigate room_e." in text


def test_turn_message_unchanged_part_and_agreement(context):
    req = TurnRequest(round=2, transcript=(
        TranscriptEntry("robot_a", "proposal", "", J(part(A_GOES))),
        TranscriptEntry("robot_b", "proposal", "", J(TAKE_TURNS)),
        TranscriptEntry("robot_a", "proposal", "same", J(TAKE_TURNS)),
    ))
    text = turn_message(req, context)
    assert "- robot_a PROPOSES ITS PART UNCHANGED, which accepts team plan version 2. same" in text
    assert "still needed: nobody." in text


def test_turn_message_before_any_part_and_with_problems(context):
    text = turn_message(TurnRequest(round=1, transcript=()), context)
    assert "Team plan: none yet." in text and "You have not proposed your part yet." in text
    assert "Robots now" in text                                   # pointer to the coordinator's line
    clash = {"robot_id": "robot_b", "tasks": [dict(B_NOW["tasks"][0], task_id="a1")]}
    req = TurnRequest(round=1, transcript=(
        TranscriptEntry("robot_a", "proposal", "", J(part(A_GOES))),
        TranscriptEntry("robot_b", "proposal", "", J({"assignments": [A_GOES, clash]})),
    ))
    text = turn_message(req, context)
    assert "Problem in the team plan: duplicate task_id(s): ['a1']" in text


def test_turn_message_part_missing(context):
    req = TurnRequest(round=1, transcript=(TranscriptEntry("robot_b", "proposal", "", J(part(B_NOW))),))
    assert "Parts still missing: robot_a." in turn_message(req, context)


def test_digest():
    entries = [TranscriptEntry("coordinator", "prompt", "m"), TranscriptEntry("robot_a", "proposal", "p"),
               TranscriptEntry("robot_b", "agreement", "")]
    assert transcript_digest(entries) == "robot_a proposal; robot_b agreement"
    assert len(transcript_digest(entries * 50, max_len=40)) == 40


def test_plan_summary():
    assert plan_summary(J(TAKE_TURNS)) == "robot_a: a1 navigate room_e | robot_b: b1 navigate room_w (starts after a1)"
    assert plan_summary("not json") == "(plan not readable)"


def test_only_tasks_count_is_in_the_system_prompt(context):
    text = system_prompt(context)
    assert "ONLY TASKS COUNT" in text and "a robot waits only if one of its tasks has a depends_on" in text


def test_words_that_promise_a_wait_do_not_show_as_a_wait(context):
    """llm_log call 57: the text said 'wait for robot_b', the tasks had no depends_on."""
    req = TurnRequest(round=2, transcript=(
        TranscriptEntry("robot_b", "proposal", "", J(part(B_NOW))),
        TranscriptEntry("robot_a", "proposal", "I will wait for robot_b to finish", J(part(A_GOES))),
    ))
    text = turn_message(req, context)
    assert "robot_a: a1 navigate room_e; robot_b: b1 navigate room_w." in text   # no 'after' anywhere


def test_action_timing_is_explained(context):
    text = system_prompt(context)
    assert "navigate: stay where you are until every depends_on task is done, THEN drive" in text
    assert "wait: drive to the target NOW, then stay there" in text
