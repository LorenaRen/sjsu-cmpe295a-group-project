"""GateSession: one ProposedPlan in, one decision out, and the fairness ledger it keeps."""
import json
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan

from fleet_checker.adapters.compiler import OnEdge
from fleet_checker.adapters.sim_map import load_sim_map
from fleet_checker.adapters.verdict import APPROVE, REJECT
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation
from fleet_checker.adapters.team_check import ALL_RULES, Rules
from mrs_ethical_gate.session import GateSession
from mrs_ethical_gate.settings import PARAMETERS, REQUIRED, SettingsError, load_settings
from mrs_ethical_gate.starts import PersonSeen, RobotState, TeamSnapshot, TeamStateStarts

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
    return GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5, robot_length=0.45)


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


def test_six_rounds_of_the_same_conflict(samples_map):
    """The run in rules_v1.md: robot_b yields until F-2 (M = 3) hands the yield to robot_a."""
    session = GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5, robot_length=0.45,
                          starvation_threshold=3)
    rounds = [list(decide(session, CONFLICT, attempt=i).result.must_replan) for i in range(6)]
    assert rounds == [["robot_b"]] * 3 + [["robot_a"]] * 3


def test_with_the_default_threshold_f2_acts_within_three_attempts(session):
    # M = 2: after two REJECTs in a row robot_b has priority, in attempt 3, the last of Module 4's.
    rounds = [list(decide(session, CONFLICT, attempt=i).result.must_replan) for i in range(3)]
    assert rounds == [["robot_b"], ["robot_b"], ["robot_a"]]


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
    session = GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5, robot_length=0.45,
                          starvation_threshold=1)
    rounds = [list(decide(session, CONFLICT, attempt=i).result.must_replan) for i in range(2)]
    assert rounds == [["robot_b"], ["robot_a"]]


def test_summary_is_plain_json_for_the_log(session):
    d = decide(session, CONFLICT)
    assert json.loads(json.dumps(d.summary())) == {
        "verdict": "REJECT", "rule_ids": ["capacity_exceeded"], "must_replan": ["robot_b"],
        "replayed": False, "new_episode": True, "ledger": ledger(0, 0, 0, 1),
        "positions": {"source": "spawns"}, "people": []}


# ---------------- S-2: people ----------------

def test_people_near_a_route_are_checked_for_crowding(samples_map):
    """In valid_two_robots robot_b waits at corridor_2 for robot_a; with a person 1.1 m from
    corridor_2, robot_b waits next to them while robot_a drives past."""
    person = HumanObservation(human_id="person_1", position=(10.0, -1.0))
    session = GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5, robot_length=0.45,
                          people=[person], crowding=CrowdingLimit(radius=1.5, max_robots=1))
    d = decide(session, VALID)
    assert (d.verdict.verdict, d.verdict.rule_ids) == (REJECT, (R.HUMAN_CROWDING,))
    assert list(d.result.must_replan) == ["robot_a"]   # robot_b was next to the person first
    assert d.ledger == ledger(0, 1, 0, 0)               # counts toward F-2 like an S-1 REJECT


# ---------------- S-4: two robots stopped at one node ----------------

BOTH_AT_ROOM_3 = TeamPlan.model_validate({"assignments": [
    {"robot_id": "robot_a", "tasks": [{"task_id": "a1", "action": "hold_position", "target": "room_3", "priority": 1}]},
    {"robot_id": "robot_b", "tasks": [{"task_id": "b1", "action": "hold_position", "target": "room_3", "priority": 1}]},
]}).model_dump_json()


def test_an_s4_conflict_only_the_keeper_can_resolve_counts_for_the_keeper(session):
    """robot_b reaches room_3 first and stays there; robot_a's task ends there. R-1 picks robot_a
    to give way, but only robot_b can free room_3, so robot_b must change its plan: the REJECT
    counts toward F-2 for robot_b, and the approval records the yield for robot_b."""
    d = decide(session, BOTH_AT_ROOM_3)
    assert (d.verdict.verdict, d.verdict.rule_ids) == (REJECT, (R.NODE_OCCUPIED,))
    assert list(d.result.must_replan) == ["robot_b"]
    assert d.ledger == ledger(0, 0, 0, 1)
    b_elsewhere = edited(BOTH_AT_ROOM_3,
                         lambda t: t.update(target="corridor_2") if t["task_id"] == "b1" else None)
    d = decide(session, b_elsewhere, attempt=1)
    assert d.verdict.verdict == APPROVE
    assert d.ledger == ledger(0, 0, 1, 0)


# ---------------- start_source team_state ----------------

def at(robot_id, node_or_edge, x, y, stamp=10.0):
    return RobotState(robot_id, node_or_edge, x, y, stamp)


AT_SPAWNS = (at("robot_a", "west_s", 0.0, -1.2), at("robot_b", "east_n", 12.0, 1.2))
IN_THE_PASSAGE = (at("robot_a", "west_s", 0.0, -1.2), at("robot_b", "pass_w", 2.6, 0.0))


LIMIT = CrowdingLimit(radius=1.5, max_robots=1)   # config/gate.yaml's placeholders


def live_session(samples_map, people=()):
    """A session on samples_world that takes the robots' positions from /mrs/team_state."""
    return GateSession(samples_map, TeamStateStarts(samples_map, ("robot_a", "robot_b"), max_age=1.0),
                       speed=0.5, robot_length=0.45, crowding=LIMIT, people=people)


@pytest.fixture
def live(samples_map):
    return live_session(samples_map)


def observe(session, *robots, episode_id="ep1", stamp=10.0, blocked_edges=()):
    session.starts.observe(TeamSnapshot(episode_id, tuple(robots), stamp, tuple(blocked_edges)))


def test_without_a_snapshot_a_plan_is_rejected_unchecked(live):
    d = decide(live, VALID, now=10.0)
    assert (d.verdict.verdict, d.verdict.rule_ids, d.result, d.validated) == (REJECT, (R.UNKNOWN_ROBOT,), None, None)
    assert [(c.kind, c.robot_id) for c in d.verdict.constraints] == [("custom", None)]
    assert d.ledger == ledger(0, 0, 0, 0)
    assert d.summary()["positions"] == {"source": "team_state", "snapshot_age_s": None, "unknown": [
        {"robot": None, "why": "no /mrs/team_state has been received yet"}]}


def test_the_plan_is_checked_where_the_snapshot_puts_the_robots(live):
    observe(live, *AT_SPAWNS)
    assert decide(live, VALID, now=10.5).verdict.verdict == APPROVE    # as from the spawn slots
    # robot_b now stands in the passage at pass_w, where robot_a has to drive through.
    observe(live, *IN_THE_PASSAGE)
    d = decide(live, VALID, attempt=1, now=10.5)
    assert d.verdict.rule_ids == (R.CAPACITY_EXCEEDED,)
    assert d.result.start_nodes == {"robot_a": "west_s", "robot_b": "pass_w"}
    assert d.summary()["positions"]["robots"]["robot_b"] == {"at": "pass_w", "age_s": 0.5}


def test_a_robot_on_an_edge_first_drives_the_rest_of_it(live):
    observe(live, AT_SPAWNS[0], at("robot_b", "corridor_1->pass_e", 8.6, 0.0))
    d = decide(live, VALID, now=10.0)
    assert d.result.on_edges == {"robot_b": OnEdge("corridor_1", "pass_e", (8.6, 0.0))}
    assert d.result.compiled.lead_ins == {"robot_b": 2}


def test_a_stale_position_rejects_the_plan_and_leaves_the_ledger_alone(live):
    observe(live, *AT_SPAWNS)
    decide(live, CONFLICT, now=10.0)                       # checked: a rejection for robot_b
    d = decide(live, CONFLICT, attempt=1, now=12.0)        # positions 2 s old: not checked
    assert d.verdict.rule_ids == (R.UNKNOWN_ROBOT,)
    assert [(c.robot_id, c.text) for c in d.verdict.constraints] == [
        (r, f"The gate does not know where {r} is: its position is 2.0 s old, more than team_state_max_age "
            f"(1 s). Send the plan again once it does.") for r in ("robot_a", "robot_b")]
    assert d.ledger == ledger(0, 0, 0, 1)


def test_an_unreadable_plan_is_malformed_before_positions_count(live):
    d = decide(live, "{not json", now=10.0)
    assert d.verdict.rule_ids == (R.LLM_MALFORMED,) and d.summary()["positions"] is None


def test_team_state_needs_a_crowding_limit_since_robots_may_see_people(samples_map):
    with pytest.raises(ValueError, match="CrowdingLimit"):
        GateSession(samples_map, TeamStateStarts(samples_map, ("robot_a", "robot_b"), max_age=1.0),
                    speed=0.5, robot_length=0.45)


def test_routes_go_around_the_snapshots_blocked_edges(live):
    # With the passage blocked, both robots of the valid sample drive through the restricted office.
    observe(live, *AT_SPAWNS, blocked_edges=["pass_w--pass_e"])
    d = decide(live, VALID, now=10.0)
    assert d.verdict.rule_ids == (R.REGION_ACCESS_DENIED,)
    assert d.result.blocked_edges == (("pass_e", "pass_w"),)
    assert d.summary()["positions"]["blocked_edges"] == ["pass_e--pass_w"]


# The person of fixtures/samples_world_people.yaml, 1.1 m from corridor_2, where valid_two_robots
# has robot_b wait while robot_a drives by.
BY_CORRIDOR_2 = PersonSeen("person_1", 10.0, -1.0)


def test_people_the_robots_see_are_checked_for_crowding(live):
    observe(live, AT_SPAWNS[0], AT_SPAWNS[1]._replace(people=(BY_CORRIDOR_2,)))
    d = decide(live, VALID, now=10.0)
    assert d.verdict.rule_ids == (R.HUMAN_CROWDING,)
    assert d.summary()["people"] == [{"id": "person_1", "x": 10.0, "y": -1.0, "from": "robot_b"}]


def test_a_person_the_robots_see_is_where_they_see_them_not_where_the_file_says(samples_map):
    file = (HumanObservation(human_id="person_1", position=(10.0, -1.0)),
            HumanObservation(human_id="person_2", position=(0.0, 4.5)))     # in the office, away from both routes
    session = live_session(samples_map, people=file)
    # Without a robot seeing person_1, the file puts them next to corridor_2.
    observe(session, *AT_SPAWNS)
    assert decide(session, VALID, now=10.0).verdict.rule_ids == (R.HUMAN_CROWDING,)
    # robot_a sees person_1 in the office now: no robot comes near them there.
    observe(session, AT_SPAWNS[0]._replace(people=(PersonSeen("person_1", 5.0, 4.5),)), AT_SPAWNS[1])
    d = decide(session, VALID, attempt=1, now=10.0)
    assert d.verdict.verdict == APPROVE
    assert d.summary()["people"] == [{"id": "person_1", "x": 5.0, "y": 4.5, "from": "robot_a"},
                                     {"id": "person_2", "x": 0.0, "y": 4.5, "from": "file"}]


def test_from_settings_reads_positions_from_team_state():
    session = GateSession.from_settings(settings(start_source="team_state", team_state_max_age=0.8))
    assert isinstance(session.starts, TeamStateStarts) and session.starts.max_age == 0.8
    assert session.starts.fleet == ("robot_a", "robot_b")
    assert decide(session, VALID).verdict.rule_ids == (R.UNKNOWN_ROBOT,)


# ---------------- from the node's settings ----------------

# Every rule and the fairness ledger on, as the tests of what each rule does need (the gate's
# defaults, in PARAMETERS and config/gate.yaml, run fewer).
ALL_ON = {"rule_s1": True, "rule_s2": True, "rule_s4": True, "rule_p1": True, "rule_p2": True, "rule_p3": True,
          "fairness_ledger": True}


def settings(rules=ALL_ON, **changes):
    """The node's settings on samples_world, the required parameters set as in config/gate.yaml, every
    rule on (rules={}: the gate's default rules)."""
    values = {name: (None if default is REQUIRED else default) for name, (_, default, _) in PARAMETERS.items()}
    values.update(speed=0.5, robot_length=0.45, crowding_radius=1.5, crowding_max_robots=1,
                  map_file=str(SAMPLES_WORLD))
    values.update(rules)
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


def test_from_settings_checks_with_the_robot_length():
    session = GateSession.from_settings(settings(robot_length=0.6))
    d = session.decide(VALID, transaction_id="t1", episode_id="ep1", attempt=0, now=0.0)
    assert d.result.settings.robot_length == 0.6


def test_from_settings_places_the_whole_fleet_at_the_spawn_slots():
    session = GateSession.from_settings(settings())
    assert session.starts.at("ep1", now=0.0).start_nodes == {"robot_a": "west_s", "robot_b": "east_n"}
    # A plan that names only robot_b finds it at slot 1, with robot_a parked at slot 0.
    only_b = TeamPlan.model_validate({"assignments": [{"robot_id": "robot_b", "tasks": [
        {"task_id": "b1", "action": "hold_position", "target": "east_n", "priority": 1}]}]})
    d = decide(session, only_b.model_dump_json())
    assert d.verdict.verdict == APPROVE
    assert set(d.result.compiled.plans) == {"robot_a", "robot_b"}
    assert d.result.compiled.snapshot.robot_regions == {"robot_a": "west_hall", "robot_b": "east_hall"}


def test_a_decision_keeps_no_recheck_memo(session):
    # Decisions stay in the session for replays; the memo a check shares with its rechecks
    # would keep every claim and sweep of every decision of the episode alive.
    d = decide(session, CONFLICT)
    assert d.result.findings and d.result.memo is None


def test_a_session_counts_two_rejects_in_a_row_as_starving_by_default(session):
    assert session.starvation_threshold == 2 == session.ledger.starvation_threshold


# ---------------- which rules the session runs ----------------

BOTH_AT_ROOM_3 = json.dumps({"assignments": [
    {"robot_id": r, "tasks": [{"task_id": t, "action": "hold_position", "target": "room_3", "priority": 1}]}
    for r, t in (("robot_a", "a1"), ("robot_b", "b1"))]})


def test_a_session_runs_every_rule_and_keeps_the_ledger_unless_told_otherwise(session):
    assert (session.rules, session.fairness_ledger) == (ALL_RULES, True)
    assert decide(session, BOTH_AT_ROOM_3).verdict.rule_ids == (R.NODE_OCCUPIED,)


def test_from_the_default_settings_the_gate_runs_the_guides_rules():
    session = GateSession.from_settings(settings(rules={}, people_file=str(SAMPLES_PEOPLE)))
    assert session.rules == Rules(s1=True, s2=False, s4=False, p1=True, p2=True, p3=False)
    assert decide(session, BOTH_AT_ROOM_3).verdict.verdict == APPROVE   # S-4 off
    assert decide(session, VALID, attempt=1).verdict.verdict == APPROVE  # S-2 off: the person is not checked
    assert decide(session, CONFLICT, attempt=2).verdict.rule_ids == (R.CAPACITY_EXCEEDED,)   # S-1 on


def test_without_the_fairness_ledger_r1_ranks_by_its_tie_breaks_and_nothing_is_recorded(samples_map):
    session = GateSession(samples_map, {"robot_a": "west_s", "robot_b": "east_n"}, speed=0.5, robot_length=0.45,
                          fairness_ledger=False)
    rounds = [decide(session, CONFLICT, attempt=i) for i in range(4)]
    assert [list(d.result.must_replan) for d in rounds] == [["robot_b"]] * 4   # no F-2 hand-over
    assert all(d.ledger == {} for d in rounds)
    assert decide(session, VALID, attempt=4).ledger == {}
