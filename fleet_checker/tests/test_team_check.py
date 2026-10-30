"""check_team_plan on the Module 1 sample plans and on Shervan's narrow_aisle map."""
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan

from checker.adapters.sim_map import load_sim_map, spawn_starts
from checker.adapters.team_check import check_team_plan, reason_of
from checker.cross_plan import FairnessLedger
from checker.plan import Violation

FIXTURES = Path(__file__).parent / "fixtures"
SPEED = 0.5  # m/s; a test value, not Module 6's speed


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(FIXTURES / "samples_world.yaml")


@pytest.fixture(scope="module")
def aisle_map():
    return load_sim_map(FIXTURES / "narrow_aisle.yaml")


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
    # With a fresh ledger and equal task types, R-1 ties and falls back to robot_id order.
    assert list(check(samples_map, plan).must_replan) == ["robot_a"]
    assert list(check(samples_map, swapped).must_replan) == ["robot_a"]


def test_the_fairness_ledger_decides_who_yields(samples_map):
    plan = drop_dependencies(sample("valid_two_robots.json"))
    ledger = FairnessLedger()
    ledger.record_yield("robot_a", conflict_id="earlier", timestamp=0.0, opponent_id="robot_b")
    # robot_a has yielded before, robot_b has not, so robot_b yields this time.
    assert list(check(samples_map, plan, ledger=ledger).must_replan) == ["robot_b"]


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


def test_narrow_aisle_swap_passes_when_one_robot_waits(aisle_map):
    assert check(aisle_map, swap_rooms(robot_b_waits=True)).approved


# ---------------- plumbing ----------------

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
    "CAPACITY_EXCEEDED", "EDGE_SWAP", "FINAL_STOP_IN_PASSTHROUGH", "REGION_ACCESS_DENIED",
    "MAX_DURATION_EXCEEDED", "UNKNOWN_ROBOT", "UNKNOWN_TARGET", "NO_ROUTE", "DEPENDENCY_DEADLOCK",
])
def test_every_code_the_pipeline_emits_is_a_reason_code(code):
    assert reason_of(Violation(code=code, message="")) == code.lower()


def test_unregistered_code_is_refused():
    with pytest.raises(ValueError, match="unknown reason code"):
        reason_of(Violation(code="ROBOT_WAS_TIRED", message=""))
