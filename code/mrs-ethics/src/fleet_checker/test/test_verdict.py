"""to_verdict: check_team_plan result -> EthicalVerdict content (REJECT path)."""
import re
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R
from mrs_common.plan_schema import TeamPlan, constraints_from_json, constraints_to_json

from fleet_checker.adapters.sim_map import load_sim_map, spawn_starts
from fleet_checker.adapters.team_check import check_team_plan
from fleet_checker.adapters.verdict import APPROVE, MODIFY, REJECT, to_verdict, unreadable_plan_verdict
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation
from fleet_checker.policy import AccessPolicy

SRC = Path(__file__).resolve().parents[2]    # the workspace's src/
SAMPLES_WORLD = Path(__file__).parent / "fixtures" / "samples_world.yaml"
NARROW_AISLE = SRC / "mrs_sim/worlds/narrow_aisle.yaml"
VERDICT_MSG = SRC / "mrs_msgs/msg/EthicalVerdict.msg"
SPEED = 0.5  # m/s; a test value, not Module 6's speed


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


@pytest.fixture(scope="module")
def aisle_map():
    return load_sim_map(NARROW_AISLE)


def sample(name: str) -> TeamPlan:
    return plan_from_json(load_sample(name))


def task(task_id, action, target, depends_on=()) -> dict:
    return {"task_id": task_id, "action": action, "target": target, "priority": 1,
            "depends_on": list(depends_on)}


def team(*assignments) -> TeamPlan:
    return TeamPlan.model_validate(
        {"assignments": [{"robot_id": r, "tasks": tasks} for r, tasks in assignments]})


def edited(plan: TeamPlan, edit) -> TeamPlan:
    data = plan.model_dump(mode="json")
    for a in data["assignments"]:
        for t in a["tasks"]:
            edit(t)
    return TeamPlan.model_validate(data)


def drop_dependencies(plan: TeamPlan) -> TeamPlan:
    return edited(plan, lambda t: t.update(depends_on=[]))


def verdict_for(sim_map, plan, starts=None, speed=SPEED, **kwargs):
    starts = starts or spawn_starts(sim_map, ["robot_a", "robot_b"])
    return to_verdict(check_team_plan(plan, sim_map, starts, speed=speed, **kwargs))


def fields(verdict, *names):
    return [tuple(getattr(c, n) for n in names) for c in verdict.constraints]


# ---------------- APPROVE ----------------

def test_valid_plan_is_approved(samples_map):
    v = verdict_for(samples_map, sample("valid_two_robots.json"))
    assert v.verdict == APPROVE
    assert v.rule_ids == () and v.constraints == () and v.standard_refs == ()
    assert v.risk_score == 0.0


# ---------------- P-1 -> forbid_node ----------------

def test_keep_clear_stop_forbids_stopping_at_door_A(samples_map):
    v = verdict_for(samples_map, sample("keep_clear_stop.json"))
    assert v.verdict == REJECT
    assert v.rule_ids == (R.FINAL_STOP_IN_PASSTHROUGH,)
    assert fields(v, "kind", "robot_id", "task_id", "node") == [("forbid_node", "robot_b", "b1", "door_A")]
    assert v.risk_score == pytest.approx(2 / 3)
    assert "door_A" in v.rationale and "keep-clear" in v.rationale


def test_p1_covers_every_node_of_the_zone(samples_map):
    # robot_b parks at pass_e; the passage also has pass_w, which must not become the next stop.
    plan = team(("robot_a", [task("a1", "hold_position", "west_s")]),
                ("robot_b", [task("b1", "hold_position", "pass_e")]))
    v = verdict_for(samples_map, plan)
    assert fields(v, "kind", "robot_id", "node") == [
        ("forbid_node", "robot_b", "pass_e"), ("forbid_node", "robot_b", "pass_w")]


# ---------------- P-2 -> avoid_node ----------------

def test_restricted_target_avoids_every_node_of_the_office(samples_map):
    v = verdict_for(samples_map, sample("restricted_target.json"))
    assert v.rule_ids == (R.REGION_ACCESS_DENIED,)
    # a1 and a2 both cross the office: one constraint per office node, not per crossing.
    assert fields(v, "kind", "robot_id", "task_id", "node") == [
        ("avoid_node", "robot_a", None, "office_1"),
        ("avoid_node", "robot_a", None, "office_e"),
        ("avoid_node", "robot_a", None, "office_w"),
    ]
    assert v.risk_score == pytest.approx(2 / 3)
    assert "office" in v.rationale and "restricted" in v.rationale


def test_p2_in_a_zone_without_nodes_is_custom(samples_map):
    # door_b holds no node, so there is nothing for avoid_node to name.
    v = verdict_for(samples_map, sample("restricted_target.json"),
                    access_policy=AccessPolicy(by_id={"door_b": frozenset()}))
    [c] = v.constraints
    assert (c.kind, c.robot_id) == ("custom", "robot_a")
    assert "door_b" in c.text


# ---------------- S-1 -> verified order_before ----------------

def test_conflict_gives_a_verified_order_before(samples_map):
    v = verdict_for(samples_map, drop_dependencies(sample("valid_two_robots.json")))
    assert v.rule_ids == (R.CAPACITY_EXCEEDED,)
    # robot_b yields (it reaches the passage later): b2 may start only when a1 is done.
    assert fields(v, "kind", "robot_id", "task_id", "value") == [("order_before", "robot_b", "a1", "b2")]
    assert v.risk_score == 1.0
    assert "passage" in v.rationale and "1 robot" in v.rationale


def test_narrow_aisle_swap_puts_robot_a_after_robot_b(aisle_map):
    plan = team(("robot_a", [task("a1", "hold_position", "room_e_s")]),
                ("robot_b", [task("b2", "hold_position", "room_w_n")]))
    v = verdict_for(aisle_map, plan)
    # Mirror-symmetric map: entry times tie, robot_id picks robot_a to yield.
    assert fields(v, "kind", "robot_id", "task_id", "value") == [("order_before", "robot_a", "b2", "a1")]


def test_head_on_swap_is_one_problem_and_one_ordering(two_narrow_zones):
    plan = team(("robot_a", [task("a1", "navigate", "er")]), ("robot_b", [task("b1", "navigate", "wr")]))
    v = verdict_for(two_narrow_zones, plan, starts={"robot_a": "wr", "robot_b": "er"}, speed=1.0)
    assert v.rule_ids == (R.EDGE_SWAP,)
    assert v.rationale.count("head-on") == 1                    # two findings, one problem
    assert fields(v, "kind", "robot_id", "task_id", "value") == [("order_before", "robot_a", "b1", "a1")]


# ---------------- S-1 where ordering does not help -> custom ----------------

def test_keeper_that_stays_in_the_zone_gets_custom(samples_map):
    # robot_b parks in the passage; robot_a waits for that, then has to cross it.
    plan = team(("robot_a", [task("a1", "navigate", "corridor_1", depends_on=["b1"])]),
                ("robot_b", [task("b1", "hold_position", "pass_e")]))
    v = verdict_for(samples_map, plan)
    assert v.rule_ids == (R.CAPACITY_EXCEEDED, R.FINAL_STOP_IN_PASSTHROUGH)
    [custom] = [c for c in v.constraints if c.rule_id == R.CAPACITY_EXCEEDED]
    assert (custom.kind, custom.robot_id) == ("custom", "robot_a")
    assert "robot_b stays in passage until the end of its plan" in custom.text
    assert {c.node for c in v.constraints if c.kind == "forbid_node"} == {"pass_e", "pass_w"}
    assert v.risk_score == 1.0                                  # the worst finding decides


def test_ordering_that_would_close_a_cycle_gets_custom(samples_map):
    # robot_a waits inside the passage for b1, while b1 has to drive through it.
    plan = team(("robot_a", [task("a1", "navigate", "pass_w"),
                             task("a2", "navigate", "corridor_1", depends_on=["b1"])]),
                ("robot_b", [task("b1", "navigate", "room_2")]))
    v = verdict_for(samples_map, plan)
    [c] = v.constraints
    assert (c.kind, c.robot_id, c.rule_id) == ("custom", "robot_b", R.CAPACITY_EXCEEDED)
    assert "cycle" in c.text


# ---------------- S-2 -> verified order_before, or custom ----------------

LIMIT = CrowdingLimit(radius=1.5, max_robots=1)   # placeholder values, as in the gate's config
BY_C = HumanObservation(human_id="person_1", position=(5.0, 1.0))         # 1 m from the crossing
BY_ROOM_E = HumanObservation(human_id="person_1", position=(12.5, 0.5))   # 0.86 m from room_e_n

# robot_a drives w -> c -> e and robot_b n -> c -> s, both past the person at c, around t = 4 s.
CROSSING = team(("robot_a", [task("a1", "navigate", "e")]), ("robot_b", [task("b1", "navigate", "s")]))


def test_crowding_at_a_crossing_gives_a_verified_order_before(crossroads):
    v = verdict_for(crossroads, CROSSING, speed=1.0, people=[BY_C], crowding=LIMIT)
    assert (v.verdict, v.rule_ids, v.risk_score) == (REJECT, (R.HUMAN_CROWDING,), 1.0)
    assert v.rationale == ("Plan rejected: robot_a and robot_b would be within 1.5 m of person_1 "
                           "(nearest node c) at the same time, but at most 1 robot may be. "
                           "Propose a new plan that follows the constraints.")
    # robot_b is next to the person first, so robot_a waits (at w, 5 m away) for b1.
    [c] = v.constraints
    assert (c.kind, c.rule_id, c.robot_id, c.task_id, c.value) == (
        "order_before", R.HUMAN_CROWDING, "robot_a", "b1", "a1")
    assert c.text == ("robot_a: start task a1 only after robot_b has finished task b1; both would be "
                      "within 1.5 m of person_1 (nearest node c), where at most 1 robot may be at once.")


def test_following_the_crowding_order_before_is_approved(crossroads):
    fixed = edited(CROSSING, lambda t: t["depends_on"].append("b1") if t["task_id"] == "a1" else None)
    assert verdict_for(crossroads, fixed, speed=1.0, people=[BY_C], crowding=LIMIT).verdict == APPROVE


def test_crowding_that_ordering_would_turn_into_a_cycle_gets_custom(aisle_map):
    # robot_b waits next to the person for robot_a; robot_a would have to wait for robot_b to leave.
    plan = team(("robot_a", [task("a1", "hold_position", "room_e_s")]),
                ("robot_b", [task("b1", "wait", "room_e_n", depends_on=["a1"]),
                             task("b2", "hold_position", "room_w_n", depends_on=["b1"])]))
    v = verdict_for(aisle_map, plan, people=[BY_ROOM_E], crowding=LIMIT)
    [c] = v.constraints
    assert (c.kind, c.rule_id, c.robot_id, c.task_id) == ("custom", R.HUMAN_CROWDING, "robot_a", "a1")
    assert c.text == ("robot_a and robot_b would be within 1.5 m of person_1 (nearest node room_e) at "
                      "the same time, but at most 1 robot may be, and making robot_a wait for robot_b "
                      "does not fix it: the tasks would wait for each other in a cycle. Change the plan "
                      "so that robot_a keeps more than 1.5 m from person_1 while robot_b is there.")


def test_a_robot_that_stays_next_to_the_person_gets_custom(aisle_map):
    plan = team(("robot_a", [task("a1", "hold_position", "room_e_s")]),
                ("robot_b", [task("b1", "hold_position", "room_e_n")]))   # already there
    v = verdict_for(aisle_map, plan, people=[BY_ROOM_E], crowding=LIMIT)
    [c] = v.constraints
    assert (c.kind, c.robot_id) == ("custom", "robot_a")
    assert "robot_b stays within 1.5 m of person_1 until the end of its plan" in c.text


# ---------------- S-4 -> verified order_before, forbid_node or custom ----------------

def s4(v):
    """The S-4 constraints of a verdict: (kind, robot, task, node, value, text)."""
    return [(c.kind, c.robot_id, c.task_id, c.node, c.value, c.text)
            for c in v.constraints if c.rule_id == R.NODE_OCCUPIED]


PARKING_THREE = {"robot_a": "w", "robot_b": "n", "robot_c": "f"}
SLOW_C = ("robot_c", [task("c1", "navigate", "h"), task("c2", "navigate", "f")])   # done at 8 s, far from c


def test_a_too_tight_handover_at_a_node_gives_a_verified_order_before(parking):
    """robot_b stops at c until robot_a reaches p, 0.5 m away; robot_a then drives on to c and
    stays. robot_b leaves at 4.5 s and robot_a arrives at 5.0 s: within the margin."""
    plan = team(("robot_a", [task("a0", "navigate", "p"), task("a1", "hold_position", "c")]),
                ("robot_b", [task("b1", "navigate", "c"), task("b2", "navigate", "s", depends_on=["a0"])]))
    v = verdict_for(parking, plan, speed=1.0)
    assert (v.verdict, v.rule_ids, v.risk_score) == (REJECT, (R.NODE_OCCUPIED,), 1.0)
    assert v.rationale == ("Plan rejected: robot_a and robot_b would stop at c at the same time. "
                           "Propose a new plan that follows the constraints.")
    assert s4(v) == [("order_before", "robot_a", "b2", None, "a1",
                      "robot_a: start task a1 only after robot_b has finished task b2; both would stop "
                      "at c, where only one robot can stand.")]
    fixed = edited(plan, lambda t: t["depends_on"].append("b2") if t["task_id"] == "a1" else None)
    assert verdict_for(parking, fixed, speed=1.0).verdict == APPROVE


def test_waiting_where_the_other_robot_stays_is_forbidden(parking):
    """robot_a reaches c (a1) and would wait there for robot_c before a2; robot_b ends at c.
    c is not where a2 goes, so robot_a can wait somewhere else."""
    plan = team(("robot_a", [task("a1", "navigate", "c"), task("a2", "navigate", "e", depends_on=["c2"])]),
                ("robot_b", [task("b1", "hold_position", "c")]), SLOW_C)
    v = verdict_for(parking, plan, starts=PARKING_THREE, speed=1.0)
    assert s4(v) == [("forbid_node", "robot_a", "a2", "c", None,
                      "robot_a: do not wait at c; robot_b stays there until the end of its plan.")]


def test_waiting_where_the_other_robot_waits_too_is_forbidden_when_ordering_cannot_help(parking):
    """As above, but robot_b leaves c only after robot_a has gone (b2 waits for a2): making
    robot_a wait for b2 would close a cycle."""
    plan = team(("robot_a", [task("a1", "navigate", "c"), task("a2", "navigate", "e", depends_on=["c2"])]),
                ("robot_b", [task("b1", "navigate", "c"), task("b2", "navigate", "s", depends_on=["a2"])]),
                SLOW_C)
    v = verdict_for(parking, plan, starts=PARKING_THREE, speed=1.0)
    assert s4(v) == [("forbid_node", "robot_a", "a2", "c", None,
                      "robot_a: do not wait at c; robot_b is stopped there until it leaves with task b2.")]


def test_a_target_held_for_a_while_means_wait_nearby_then_go(parking):
    """c is where a1 ends. robot_b waits at c until a1 is done, so ordering a1 after b2 would
    close a cycle; forbidding c would make robot_a give up a1. It is told to wait nearby."""
    plan = team(("robot_a", [task("a1", "hold_position", "c")]),
                ("robot_b", [task("b1", "navigate", "c"), task("b2", "navigate", "s", depends_on=["a1"])]))
    v = verdict_for(parking, plan, speed=1.0)
    assert s4(v) == [("custom", "robot_a", "a1", "c", None,
                      "robot_a: c is where task a1 ends, but robot_b is stopped there until it leaves "
                      "with task b2. Wait at another node near c first, and go to c only after robot_b "
                      "has left it.")]
    assert all(c.kind != "forbid_node" for c in v.constraints)


def test_a_target_held_to_the_end_cannot_be_reached_as_planned(parking):
    """Both robots end at c; robot_b gets there first and stays."""
    plan = team(("robot_a", [task("a1", "hold_position", "c")]), ("robot_b", [task("b1", "hold_position", "c")]))
    v = verdict_for(parking, plan, speed=1.0)
    assert s4(v) == [("custom", "robot_a", "a1", "c", None,
                      "robot_a: task a1 cannot be done with the plan as it is: its target c is where "
                      "robot_b stays until the end of its plan. Change where robot_b ends, so that c "
                      "is free when robot_a gets there.")]


def test_a_yielder_standing_at_the_node_from_its_start_gets_custom(parking):
    """robot_a ends at n, where robot_b stands from the start; the ledger makes robot_b give way."""
    from fleet_checker.cross_plan import FairnessLedger
    ledger = FairnessLedger()
    ledger.record_yield("robot_a", "earlier", 0.0, "robot_b")
    plan = team(("robot_a", [task("a1", "hold_position", "n")]))
    v = verdict_for(parking, plan, speed=1.0, ledger=ledger)
    assert s4(v) == [("custom", "robot_b", None, "n", None,
                      "robot_b stands at n from the start of its plan, and robot_a would stop there "
                      "while it does. Have robot_b leave n before robot_a gets there.")]


# ---------------- P-3 and compile errors -> custom ----------------

def test_too_long_plan_is_custom(samples_map):
    v = verdict_for(samples_map, sample("valid_two_robots.json"), max_duration=30.0)
    [c] = v.constraints
    assert (c.kind, c.robot_id) == ("custom", "robot_b")
    assert "41 s" in c.text and "30 s" in c.text
    assert v.risk_score == pytest.approx(1 / 3)


def test_compile_error_is_custom(samples_map):
    plan = edited(sample("valid_two_robots.json"),
                  lambda t: t.update(target="room_9") if t["task_id"] == "a2" else None)
    v = verdict_for(samples_map, plan)
    assert v.rule_ids == (R.UNKNOWN_TARGET,)
    [c] = v.constraints
    assert c.kind == "custom" and "room_9" in c.text
    assert v.risk_score == pytest.approx(1 / 3)


# ---------------- plan_json that is not a TeamPlan -> custom ----------------

def read_error(text: str) -> Exception:
    with pytest.raises(ValueError) as e:      # pydantic's ValidationError is a ValueError
        plan_from_json(text)
    return e.value


def test_unreadable_plan_is_rejected_as_malformed():
    v = unreadable_plan_verdict(read_error(load_sample("invalid_bad_action.json")))
    assert (v.verdict, v.rule_ids, v.standard_refs) == (REJECT, (R.LLM_MALFORMED,), ())
    assert v.risk_score == pytest.approx(1 / 3)
    [c] = v.constraints
    assert (c.kind, c.rule_id, c.robot_id) == ("custom", R.LLM_MALFORMED, None)
    # Names the field and what is allowed, like plan_echo does.
    assert "assignments.0.tasks.0.action: Input should be 'navigate', 'wait' or 'hold_position'" in c.text
    assert v.rationale.startswith("Plan rejected: plan_json is not a valid TeamPlan (")
    assert tuple(constraints_from_json(constraints_to_json(v.constraints))) == v.constraints


def test_plan_level_and_json_errors_are_described_without_a_location():
    duplicate = unreadable_plan_verdict(read_error(load_sample("invalid_duplicate_task.json")))
    assert "duplicate task_id(s): ['t1']" in duplicate.constraints[0].text
    empty = unreadable_plan_verdict(read_error(""))
    assert "Invalid JSON" in empty.constraints[0].text


# ---------------- rationale ----------------

def test_rationale_is_problems_then_what_to_do(samples_map):
    v = verdict_for(samples_map, sample("keep_clear_stop.json"))
    assert v.rationale == ("Plan rejected: robot_b would stop at door_A in door_a, a keep-clear zone "
                           "that must stay clear. Propose a new plan that follows the constraints.")


def test_rationale_lists_at_most_three_problems(samples_map):
    plan = team(("robot_a", [task("a1", "navigate", "corridor_1", depends_on=["b1"])]),
                ("robot_b", [task("b1", "hold_position", "pass_e")]))
    v = verdict_for(samples_map, plan, max_duration=10.0)       # adds P-3 for both robots
    assert "; and 1 more problem." in v.rationale


# ---------------- what goes into the message ----------------

def test_constraints_survive_constraints_json(samples_map):
    v = verdict_for(samples_map, sample("restricted_target.json"))
    assert tuple(constraints_from_json(constraints_to_json(v.constraints))) == v.constraints


def test_make_verdict_kwargs(samples_map):
    v = verdict_for(samples_map, sample("keep_clear_stop.json"))
    kwargs = v.make_verdict_kwargs()
    assert set(kwargs) == {"verdict", "rule_ids", "standard_refs", "risk_score", "rationale", "constraints"}
    assert kwargs["standard_refs"] == [] and kwargs["rule_ids"] == [R.FINAL_STOP_IN_PASSTHROUGH]


def test_verdict_strings_match_ethical_verdict_msg():
    found = dict(re.findall(r"^string VERDICT_(\w+)=(\w+)$", VERDICT_MSG.read_text(), re.M))
    assert found == {"APPROVE": APPROVE, "MODIFY": MODIFY, "REJECT": REJECT}
