"""check_team_plan on the Module 1 sample plans and on Shervan's narrow_aisle map."""
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan

from fleet_checker.adapters.sim_map import load_sim_map, spawn_starts
from fleet_checker.adapters.team_check import check_team_plan, conflicts, reason_of
from fleet_checker.cross_plan import FairnessLedger
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation, Violation

SAMPLES_WORLD = Path(__file__).parent / "fixtures" / "samples_world.yaml"
NARROW_AISLE = Path(__file__).resolve().parents[2] / "mrs_sim/worlds/narrow_aisle.yaml"  # parents[2] = src/
SPEED = 0.5  # m/s; a test value, not Module 6's speed


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


@pytest.fixture(scope="module")
def aisle_map():
    return load_sim_map(NARROW_AISLE)


def sample(name: str) -> TeamPlan:
    return plan_from_json(load_sample(name))


def edited(plan: TeamPlan, edit) -> TeamPlan:
    """A copy of plan with edit(task_dict) applied to every task."""
    data = plan.model_dump(mode="json")
    for a in data["assignments"]:
        for t in a["tasks"]:
            edit(t)
    return TeamPlan.model_validate(data)


def check(sim_map, plan, **kwargs):
    starts = spawn_starts(sim_map, ["robot_a", "robot_b"])
    return check_team_plan(plan, sim_map, starts, speed=SPEED, **kwargs)


def codes(result):
    return [f.violation.code for f in result.findings]


# ---------------- the three schema-valid samples ----------------

def test_valid_two_robots_is_approved(samples_map):
    result = check(samples_map, sample("valid_two_robots.json"))
    assert result.approved
    assert result.findings == () and result.must_replan == {}


def test_keep_clear_stop_triggers_p1_for_robot_b_only(samples_map):
    result = check(samples_map, sample("keep_clear_stop.json"))
    assert codes(result) == ["FINAL_STOP_IN_PASSTHROUGH"]
    assert list(result.must_replan) == ["robot_b"]
    finding = result.findings[0]
    assert finding.reason == R.FINAL_STOP_IN_PASSTHROUGH
    assert finding.task_ids == ("b1",)
    # The step points back at the node it stops on, which is what a forbid_node
    # constraint for the planner would name.
    ref = finding.violation.refs[0]
    assert result.compiled.origins["robot_b"][ref.step_index].nodes == ("door_A",)


def test_restricted_target_triggers_p2_for_robot_a_only(samples_map):
    result = check(samples_map, sample("restricted_target.json"))
    assert set(codes(result)) == {"REGION_ACCESS_DENIED"}
    assert list(result.must_replan) == ["robot_a"]
    # Both of robot_a's tasks drive through the office: a1 into it, a2 out of it.
    assert [f.task_ids for f in result.findings] == [("a1",), ("a2",)]
    assert all("restricted" in f.violation.message for f in result.findings)


# ---------------- the approval is not vacuous ----------------

def drop_dependencies(plan: TeamPlan) -> TeamPlan:
    return edited(plan, lambda t: t.update(depends_on=[]))


def test_without_its_wait_valid_two_robots_meets_in_the_passage(samples_map):
    result = check(samples_map, drop_dependencies(sample("valid_two_robots.json")))
    assert codes(result) == ["CAPACITY_EXCEEDED"]
    assert "passage" in result.findings[0].violation.message
    assert len(result.must_replan) == 1     # 2 robots in a capacity-1 zone: one yields


# ---------------- priority is read, not used for yielding ----------------

def test_priority_does_not_decide_who_yields(samples_map):
    plan = drop_dependencies(sample("valid_two_robots.json"))
    swapped = edited(plan, lambda t: t.update(priority=3 - t["priority"]))   # 1 <-> 2
    assert {o.priority for o in check(samples_map, swapped).compiled.origins["robot_a"]} == {2}
    # Fresh ledger and equal task types: R-1 falls to entry time. robot_b enters
    # the passage later (16.4 s against 6.4 s), so it yields whatever the priorities say.
    assert list(check(samples_map, plan).must_replan) == ["robot_b"]
    assert list(check(samples_map, swapped).must_replan) == ["robot_b"]


def test_the_fairness_ledger_decides_who_yields(samples_map):
    plan = drop_dependencies(sample("valid_two_robots.json"))
    ledger = FairnessLedger()
    ledger.record_yield("robot_b", conflict_id="earlier", timestamp=0.0, opponent_id="robot_a")
    # robot_b has yielded before, robot_a has not, so robot_a yields this time,
    # although it enters the passage first.
    assert list(check(samples_map, plan, ledger=ledger).must_replan) == ["robot_a"]


# ---------------- Shervan's narrow_aisle ----------------

def swap_rooms(robot_b_waits: bool) -> TeamPlan:
    robot_b = [{"task_id": "b2", "action": "hold_position", "target": "room_w_n", "priority": 1}]
    if robot_b_waits:
        # Wait at its spawn node until robot_a is through, then cross.
        robot_b = [{"task_id": "b1", "action": "wait", "target": "room_e_n", "priority": 1,
                    "depends_on": ["a1"]},
                   {**robot_b[0], "depends_on": ["b1"]}]
    return TeamPlan.model_validate({"assignments": [
        {"robot_id": "robot_a", "tasks": [
            {"task_id": "a1", "action": "hold_position", "target": "room_e_s", "priority": 1}]},
        {"robot_id": "robot_b", "tasks": robot_b},
    ]})


def test_narrow_aisle_swap_conflicts_in_the_aisle(aisle_map):
    result = check(aisle_map, swap_rooms(robot_b_waits=False))
    assert codes(result) == ["CAPACITY_EXCEEDED"]
    assert "aisle" in result.findings[0].violation.message


def test_symmetric_swap_ties_on_entry_time_and_falls_back_to_robot_id(aisle_map):
    # The map is mirror-symmetric: both robots reach the aisle 3.2 m from their spawn.
    assert list(check(aisle_map, swap_rooms(robot_b_waits=False)).must_replan) == ["robot_a"]


def test_narrow_aisle_swap_passes_when_one_robot_waits(aisle_map):
    assert check(aisle_map, swap_rooms(robot_b_waits=True)).approved


# ---------------- conflicts(): the S-1 conflicts, for the ledger ----------------

def test_a_conflict_names_its_zone_robots_and_yielder(samples_map):
    [c] = conflicts(check(samples_map, drop_dependencies(sample("valid_two_robots.json"))))
    assert (c.reason, c.where, c.robots, c.yielders, c.keepers) == (
        R.CAPACITY_EXCEEDED, "passage", ("robot_a", "robot_b"), ("robot_b",), ("robot_a",))
    assert c.key == "capacity_exceeded@passage:robot_a+robot_b"


def test_a_head_on_swap_is_one_conflict(two_narrow_zones):
    plan = TeamPlan.model_validate({"assignments": [
        {"robot_id": "robot_a", "tasks": [{"task_id": "a1", "action": "navigate", "target": "er", "priority": 1}]},
        {"robot_id": "robot_b", "tasks": [{"task_id": "b1", "action": "navigate", "target": "wr", "priority": 1}]},
    ]})
    result = check_team_plan(plan, two_narrow_zones, {"robot_a": "wr", "robot_b": "er"}, speed=1.0)
    assert codes(result) == ["EDGE_SWAP", "EDGE_SWAP"]           # one finding per zone
    [c] = conflicts(result)
    assert (c.reason, c.where, c.robots, c.yielders) == (R.EDGE_SWAP, None, ("robot_a", "robot_b"), ("robot_a",))
    assert c.key == "edge_swap@-:robot_a+robot_b"


def test_policy_findings_and_approved_plans_have_no_conflicts(samples_map):
    assert conflicts(check(samples_map, sample("keep_clear_stop.json"))) == ()
    assert conflicts(check(samples_map, sample("valid_two_robots.json"))) == ()


# ---------------- S-2: people near the routes ----------------

LIMIT = CrowdingLimit(radius=1.5, max_robots=1)   # placeholder values, as in the gate's config
BY_ROOM_E = HumanObservation(human_id="person_1", position=(12.5, 0.5))   # 0.86 m from room_e_n


def test_a_person_next_to_the_waiting_robot_makes_the_swap_crowd_them(aisle_map):
    """The swap that passes S-1 has robot_b wait at its spawn, room_e_n, for robot_a to come
    through. With a person standing in room_e, robot_b waits 0.86 m from them while robot_a
    drives past them to room_e_s."""
    assert check(aisle_map, swap_rooms(robot_b_waits=True)).approved
    result = check(aisle_map, swap_rooms(robot_b_waits=True), people=[BY_ROOM_E], crowding=LIMIT)
    assert codes(result) == ["HUMAN_CROWDING"]
    [f] = result.findings
    assert (f.reason, f.violation.resource_id) == (R.HUMAN_CROWDING, "person_1")
    assert f.task_ids == ("a1", "b1", "b2")
    assert list(result.must_replan) == ["robot_a"]   # robot_b was next to the person first


def test_swapping_the_other_way_round_keeps_them_apart(aisle_map):
    """robot_b crosses first, leaving the person behind; robot_a waits in room_w."""
    plan = TeamPlan.model_validate({"assignments": [
        {"robot_id": "robot_a", "tasks": [
            {"task_id": "a1", "action": "wait", "target": "room_w_s", "priority": 1, "depends_on": ["b1"]},
            {"task_id": "a2", "action": "hold_position", "target": "room_e_s", "priority": 1,
             "depends_on": ["a1"]}]},
        {"robot_id": "robot_b", "tasks": [
            {"task_id": "b1", "action": "hold_position", "target": "room_w_n", "priority": 1}]},
    ]})
    assert check(aisle_map, plan, people=[BY_ROOM_E], crowding=LIMIT).approved


def test_people_far_from_every_route_change_nothing(samples_map):
    in_the_office = HumanObservation(human_id="person_1", position=(5.0, 4.5))
    result = check(samples_map, sample("valid_two_robots.json"), people=[in_the_office], crowding=LIMIT)
    assert result.approved


def test_recheck_keeps_the_people_and_the_limit(aisle_map):
    result = check(aisle_map, swap_rooms(robot_b_waits=True), people=[BY_ROOM_E], crowding=LIMIT)
    again = result.recheck(result.team_plan)
    assert (again.people, again.settings.crowding) == ((BY_ROOM_E,), LIMIT)
    assert codes(again) == ["HUMAN_CROWDING"]


def test_people_without_a_limit_are_refused(aisle_map):
    with pytest.raises(ValueError, match="CrowdingLimit"):
        check(aisle_map, swap_rooms(robot_b_waits=True), people=[BY_ROOM_E])


def test_a_crowding_conflict_names_the_person(aisle_map):
    [c] = conflicts(check(aisle_map, swap_rooms(robot_b_waits=True), people=[BY_ROOM_E], crowding=LIMIT))
    assert (c.reason, c.where, c.robots, c.yielders) == (
        R.HUMAN_CROWDING, "person_1", ("robot_a", "robot_b"), ("robot_a",))
    assert c.key == "human_crowding@person_1:robot_a+robot_b"


# ---------------- plumbing ----------------

def test_each_finding_says_which_robots_must_fix_it(samples_map):
    [p1] = check(samples_map, sample("keep_clear_stop.json")).findings
    assert p1.must_fix == ("robot_b",)                  # policy: the robot that broke it
    [s1] = check(samples_map, drop_dependencies(sample("valid_two_robots.json"))).findings
    assert s1.must_fix == ("robot_b",)                  # conflict: the robot R-1 chose
    plan = edited(sample("valid_two_robots.json"),
                  lambda t: t.update(target="room_9") if t["task_id"] == "a2" else None)
    [compile_error] = check(samples_map, plan).findings
    assert compile_error.must_fix == ()                 # the whole plan goes back


def test_recheck_runs_the_same_check_on_another_plan(samples_map):
    result = check(samples_map, drop_dependencies(sample("valid_two_robots.json")), max_duration=30.0)
    assert codes(result) == ["MAX_DURATION_EXCEEDED", "CAPACITY_EXCEEDED"]
    again = result.recheck(sample("valid_two_robots.json"))
    assert again.team_plan == sample("valid_two_robots.json")
    # The conflict is gone with the depends_on back; the 30 s limit still applies.
    assert codes(again) == ["MAX_DURATION_EXCEEDED"]

def test_compile_errors_stop_before_the_rule_checks(samples_map):
    plan = edited(sample("valid_two_robots.json"),
                  lambda t: t.update(target="room_9") if t["task_id"] == "a2" else None)
    result = check(samples_map, plan)
    assert codes(result) == ["UNKNOWN_TARGET"]
    assert result.findings[0].reason == R.UNKNOWN_TARGET
    assert result.must_replan == {} and result.compiled.plans == {}


def test_max_duration_is_passed_to_p3(samples_map):
    # robot_b reaches room_2 at 41.4 s, robot_a holds from 26.4 s.
    result = check(samples_map, sample("valid_two_robots.json"), max_duration=30.0)
    assert codes(result) == ["MAX_DURATION_EXCEEDED"]
    assert list(result.must_replan) == ["robot_b"]


@pytest.mark.parametrize("code", [
    "CAPACITY_EXCEEDED", "EDGE_SWAP", "HUMAN_CROWDING", "NODE_OCCUPIED", "FINAL_STOP_IN_PASSTHROUGH",
    "REGION_ACCESS_DENIED", "MAX_DURATION_EXCEEDED", "UNKNOWN_ROBOT", "UNKNOWN_TARGET", "NO_ROUTE",
    "DEPENDENCY_DEADLOCK",
])
def test_every_code_the_pipeline_emits_is_a_reason_code(code):
    assert reason_of(Violation(code=code, message="")) == code.lower()


def test_unregistered_code_is_refused():
    with pytest.raises(ValueError, match="unknown reason code"):
        reason_of(Violation(code="ROBOT_WAS_TIRED", message=""))
