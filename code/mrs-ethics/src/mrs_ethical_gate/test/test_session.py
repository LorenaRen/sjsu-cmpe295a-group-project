"""GateSession: one ProposedPlan in, one decision out, and the fairness ledger it keeps."""
import json
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan

from fleet_checker.adapters.sim_map import load_sim_map
from fleet_checker.adapters.verdict import APPROVE, REJECT
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation
from mrs_ethical_gate.session import GateSession
from mrs_ethical_gate.settings import PARAMETERS, REQUIRED, SettingsError, load_settings

SRC = Path(__file__).resolve().parents[2]
SAMPLES_WORLD = SRC / "fleet_checker/test/fixtures/samples_world.yaml"
POLICY = Path(__file__).resolve().parents[1] / "config" / "policy.yaml"
NOBODY = Path(__file__).resolve().parents[1] / "config" / "people.yaml"
SAMPLES_PEOPLE = Path(__file__).parent / "fixtures" / "samples_world_people.yaml"


def edited(text: str, edit) -> str:
    """plan_json with edit(task_dict) applied to every task."""
    data = plan_from_json(text).model_dump(mode="json")
    for a in data["assignments"]:
        for t in a["tasks"]:
            edit(t)
    return TeamPlan.model_validate(data).model_dump_json()


VALID = load_sample("valid_two_robots.json")                         # robot_b waits for robot_a
CONFLICT = edited(VALID, lambda t: t.update(depends_on=[]))         # ... and now it does not
KEEP_CLEAR_STOP = load_sample("keep_clear_stop.json")
UNKNOWN_TARGET = edited(VALID, lambda t: t.update(target="room_9") if t["task_id"] == "a2" else None)


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


@pytest.fixture
def session(samples_map):
    # robot_a and robot_b at samples_world's two spawn slots.
    return GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5)


def decide(session, plan_json, attempt=0, *, transaction_id="t1", episode_id="ep1", now=0.0):
    return session.decide(plan_json, transaction_id=transaction_id, episode_id=episode_id,
                          attempt=attempt, now=now)


def ledger(yields_a, rejections_a, yields_b, rejections_b):
    return {"robot_a": {"yields": yields_a, "rejections_in_a_row": rejections_a},
            "robot_b": {"yields": yields_b, "rejections_in_a_row": rejections_b}}


# ---------------- one plan in, one decision out ----------------

def test_an_approved_plan_comes_back_for_the_validated_plan_topic(session):
    d = decide(session, VALID)
    assert d.verdict.verdict == APPROVE
    assert d.validated == plan_from_json(VALID)
    assert (d.replayed, d.new_episode) == (False, True)


def test_a_rejected_plan_does_not(session):
    d = decide(session, KEEP_CLEAR_STOP)
    assert (d.verdict.verdict, d.verdict.rule_ids) == (REJECT, (R.FINAL_STOP_IN_PASSTHROUGH,))
    assert d.validated is None


def test_an_unreadable_plan_is_rejected_as_malformed(session):
    d = decide(session, load_sample("invalid_bad_action.json"))
    assert (d.verdict.verdict, d.verdict.rule_ids) == (REJECT, (R.LLM_MALFORMED,))
    assert d.validated is None and d.result is None


def test_the_plan_is_checked_under_its_message_ids(session):
    d = decide(session, VALID, attempt=2, transaction_id="txn-9")
    assert (d.result.compiled.plans["robot_a"].plan_id, d.result.compiled.plans["robot_a"].revision) == (
        "txn-9/robot_a", 2)


# ---------------- messages seen before ----------------

def test_a_message_seen_before_gets_the_same_verdict_and_leaves_the_ledger_alone(session):
    first = decide(session, CONFLICT)
    again = decide(session, CONFLICT)                    # same transaction_id and attempt
    assert again.replayed and again.verdict == first.verdict
    assert again.ledger == ledger(0, 0, 0, 1)


def test_a_later_attempt_of_the_same_transaction_is_checked_again(session):
    decide(session, CONFLICT, attempt=0)
    d = decide(session, CONFLICT, attempt=1)
    assert not d.replayed
    assert d.ledger == ledger(0, 0, 0, 2)


# ---------------- the fairness ledger (rules_v1.md, "Updating the ledger") ----------------

def test_a_conflict_counts_one_rejection_for_the_yielder_and_no_yield(session):
    d = decide(session, CONFLICT)
    assert list(d.result.must_replan) == ["robot_b"]
    assert d.ledger == ledger(0, 0, 0, 1)


def test_approval_records_one_yield_per_resolved_conflict_and_resets_rejections(session):
    decide(session, CONFLICT, attempt=0, now=1.0)
    decide(session, CONFLICT, attempt=1, now=2.0)        # the same conflict again: still one conflict
    d = decide(session, VALID, attempt=2, now=3.0)       # fixed: robot_b waits for robot_a
    assert d.verdict.verdict == APPROVE
    assert d.ledger == ledger(0, 0, 1, 0)
    [record] = session.ledger.yields["robot_b"]
    assert (record.conflict_id, record.timestamp, record.opponent_robot_id) == (
        "ep1/capacity_exceeded@passage:robot_a+robot_b", 3.0, "robot_a")
    # The approval used the remembered conflict up: the next approval adds nothing.
    assert decide(session, VALID, attempt=3).ledger == ledger(0, 0, 1, 0)


def test_six_rounds_of_the_same_conflict(session):
    """The run in rules_v1.md: robot_b yields until F-2 (M = 3) hands the yield to robot_a."""
    rounds = [list(decide(session, CONFLICT, attempt=i).result.must_replan) for i in range(6)]
    assert rounds == [["robot_b"]] * 3 + [["robot_a"]] * 3


def test_policy_rejections_do_not_count_toward_starvation(session):
    for i in range(3):
        d = decide(session, KEEP_CLEAR_STOP, attempt=i)
    assert d.ledger == ledger(0, 0, 0, 0)


def test_compile_errors_and_unreadable_plans_leave_the_ledger_alone(session):
    decide(session, CONFLICT, attempt=0)
    assert decide(session, UNKNOWN_TARGET, attempt=1).ledger == ledger(0, 0, 0, 1)
    assert decide(session, "{not json", attempt=2).ledger == ledger(0, 0, 0, 1)
    # The conflict is still remembered, so the approval credits robot_b's yield.
    assert decide(session, VALID, attempt=3).ledger == ledger(0, 0, 1, 0)


def test_a_new_episode_starts_from_scratch(session):
    decide(session, CONFLICT, episode_id="ep1")
    d = decide(session, CONFLICT, episode_id="ep2")      # same transaction_id and attempt
    assert d.new_episode and not d.replayed
    assert d.ledger == ledger(0, 0, 0, 1)


def test_the_starvation_threshold_is_the_sessions(samples_map):
    session = GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5,
                          starvation_threshold=1)
    rounds = [list(decide(session, CONFLICT, attempt=i).result.must_replan) for i in range(2)]
    assert rounds == [["robot_b"], ["robot_a"]]


def test_summary_is_plain_json_for_the_log(session):
    d = decide(session, CONFLICT)
    assert json.loads(json.dumps(d.summary())) == {
        "verdict": "REJECT", "rule_ids": ["capacity_exceeded"], "must_replan": ["robot_b"],
        "replayed": False, "new_episode": True, "ledger": ledger(0, 0, 0, 1)}


# ---------------- S-2: people ----------------

def test_people_near_a_route_are_checked_for_crowding(samples_map):
    """In valid_two_robots robot_b waits at corridor_2 for robot_a; with a person 1.1 m from
    corridor_2, robot_b waits next to them while robot_a drives past."""
    person = HumanObservation(human_id="person_1", position=(10.0, -1.0))
    session = GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5,
                          people=[person], crowding=CrowdingLimit(radius=1.5, max_robots=1))
    d = decide(session, VALID)
    assert (d.verdict.verdict, d.verdict.rule_ids) == (REJECT, (R.HUMAN_CROWDING,))
    assert list(d.result.must_replan) == ["robot_a"]   # robot_b was next to the person first
    assert d.ledger == ledger(0, 1, 0, 0)               # counts toward F-2 like an S-1 REJECT


# ---------------- from the node's settings ----------------

def settings(**changes):
    """The node's settings on samples_world, the required parameters set as in config/gate.yaml."""
    values = {name: (None if default is REQUIRED else default) for name, (_, default, _) in PARAMETERS.items()}
    values.update(speed=0.5, crowding_radius=1.5, crowding_max_robots=1, map_file=str(SAMPLES_WORLD))
    values.update(changes)
    return load_settings(values, default_policy_file=POLICY, default_people_file=NOBODY)


def test_from_settings_reads_the_people_file():
    assert decide(GateSession.from_settings(settings()), VALID).verdict.verdict == APPROVE   # nobody
    session = GateSession.from_settings(settings(people_file=str(SAMPLES_PEOPLE)))
    assert decide(session, VALID).verdict.rule_ids == (R.HUMAN_CROWDING,)


def test_a_person_outside_every_zone_is_refused(tmp_path):
    path = tmp_path / "people.yaml"
    path.write_text("people:\n  - {id: person_1, x: 100.0, y: -1.0}\n")   # 100 m, not 10 m
    with pytest.raises(SettingsError, match=r"person_1 at \(100, -1\) is outside every zone"):
        GateSession.from_settings(settings(people_file=str(path)))


def test_from_settings_places_the_whole_fleet_at_the_spawn_slots():
    session = GateSession.from_settings(settings())
    assert session.start_nodes == {"robot_a": "west_s", "robot_b": "east_n"}
    # A plan that names only robot_b finds it at slot 1, with robot_a parked at slot 0.
    only_b = TeamPlan.model_validate({"assignments": [{"robot_id": "robot_b", "tasks": [
        {"task_id": "b1", "action": "hold_position", "target": "east_n", "priority": 1}]}]})
    d = decide(session, only_b.model_dump_json())
    assert d.verdict.verdict == APPROVE
    assert set(d.result.compiled.plans) == {"robot_a", "robot_b"}
    assert d.result.compiled.snapshot.robot_regions == {"robot_a": "west_hall", "robot_b": "east_hall"}
