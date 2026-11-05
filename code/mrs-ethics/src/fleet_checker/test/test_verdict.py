"""to_verdict: check_team_plan result -> EthicalVerdict content (REJECT path)."""
import re
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R
from mrs_common.plan_schema import Constraint, TeamPlan, constraints_from_json, constraints_to_json

from fleet_checker.adapters.compiler import OnEdge
from fleet_checker.adapters.sim_map import load_sim_map, spawn_starts
from fleet_checker.adapters.team_check import check_team_plan
from fleet_checker.adapters.verdict import APPROVE, MODIFY, REJECT, to_verdict, unreadable_plan_verdict
from fleet_checker.cross_plan import FairnessLedger
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation
from fleet_checker.policy import AccessPolicy

SRC = Path(__file__).resolve().parents[2]    # the workspace's src/
SAMPLES_WORLD = Path(__file__).parent / "fixtures" / "samples_world.yaml"
NARROW_AISLE = SRC / "mrs_sim/worlds/narrow_aisle.yaml"
VERDICT_MSG = SRC / "mrs_msgs/msg/EthicalVerdict.msg"
SPEED = 0.5  # m/s; a test value, not Module 6's speed
ROBOT_LENGTH = 0.45   # m; mrs_bot.sdf's chassis, the gate's placeholder (config/gate.yaml)


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
    v = to_verdict(check_team_plan(plan, sim_map, starts, speed=speed, robot_length=ROBOT_LENGTH, **kwargs))
    assert len(v.cases) == len(v.constraints) and all(v.cases)   # what situation each constraint answers
    return v


def fields(verdict, *names):
    return [tuple(getattr(c, n) for n in names) for c in verdict.constraints]


# ---------------- APPROVE ----------------

def test_valid_plan_is_approved(samples_map):
    v = verdict_for(samples_map, sample("valid_two_robots.json"))
    assert v.verdict == APPROVE
    assert v.rule_ids == () and v.constraints == () and v.standard_refs == () and v.cases == ()
    assert v.risk_score == 0.0


# ---------------- P-1 -> forbid_node ----------------

def test_keep_clear_stop_forbids_stopping_at_door_A(samples_map):
    v = verdict_for(samples_map, sample("keep_clear_stop.json"))
    assert v.verdict == REJECT
    assert v.rule_ids == (R.FINAL_STOP_IN_PASSTHROUGH,)
    assert fields(v, "kind", "robot_id", "task_id", "node") == [("forbid_node", "robot_b", "b1", "door_A")]
    assert v.risk_score == pytest.approx(2 / 3)
    assert "door_A" in v.rationale and "keep-clear" in v.rationale
    assert v.cases == ("stop",)


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
    assert v.cases == ("access",) * 3


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
    assert v.cases == ("ordering",)


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
    # The two robots also drive through c 1 s apart (S-4): two problems, one demand.
    assert (v.verdict, v.rule_ids, v.risk_score) == (REJECT, (R.HUMAN_CROWDING, R.NODE_OCCUPIED), 1.0)
    assert v.rationale == ("Plan rejected: robot_a and robot_b would be within 1.5 m of person_1 "
                           "(nearest node c) at the same time, but at most 1 robot may be; robot_a and "
                           "robot_b would drive through c at the same time. "
                           "Propose a new plan that follows the constraints.")
    # robot_b is next to the person first, so robot_a waits (at w, 5 m away) for b1. S-4 asks for
    # the same ordering, which is sent once.
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


def s4_cases(v):
    """The situation each S-4 constraint of a verdict answers (VerdictContent.cases)."""
    return [case for c, case in zip(v.constraints, v.cases) if c.rule_id == R.NODE_OCCUPIED]


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
    assert s4_cases(v) == ["yielder_waits"]
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
    assert s4_cases(v) == ["no_waiting_there"]


def test_waiting_where_the_other_robot_waits_too_is_forbidden_when_ordering_cannot_help(parking):
    """As above, but robot_b leaves c only after robot_a has gone (b2 waits for a2): making
    robot_a wait for b2 would close a cycle."""
    plan = team(("robot_a", [task("a1", "navigate", "c"), task("a2", "navigate", "e", depends_on=["c2"])]),
                ("robot_b", [task("b1", "navigate", "c"), task("b2", "navigate", "s", depends_on=["a2"])]),
                SLOW_C)
    v = verdict_for(parking, plan, starts=PARKING_THREE, speed=1.0)
    assert s4(v) == [("forbid_node", "robot_a", "a2", "c", None,
                      "robot_a: do not wait at c; robot_b is stopped there until it leaves with task b2.")]
    assert s4_cases(v) == ["no_waiting_there"]


def test_a_keeper_waiting_where_the_yielders_task_ends_waits_somewhere_else(parking):
    """c is where a1 ends. robot_b waits at c until a1 is done, so ordering a1 after b2 would
    close a cycle, and forbidding c would make robot_a give up a1: robot_b, which waits at c
    for robot_a, waits somewhere else (team_check: the keeper must fix it)."""
    plan = team(("robot_a", [task("a1", "hold_position", "c")]),
                ("robot_b", [task("b1", "navigate", "c"), task("b2", "navigate", "s", depends_on=["a1"])]))
    v = verdict_for(parking, plan, speed=1.0)
    assert s4(v) == [("forbid_node", "robot_b", "b2", "c", None,
                      "robot_b: do not wait at c. robot_a has to get to c with task a1, and your task b2 "
                      "cannot start before that is done, so robot_a cannot wait for you to leave. Wait at "
                      "another node until task b2 may start.")]
    assert s4_cases(v) == ["keeper_in_the_way"]


def test_a_target_held_to_the_end_cannot_be_reached_as_planned(parking):
    """Both robots end at c; robot_b gets there first and stays. R-1 picks robot_a to give way,
    but only robot_b can free c: the custom constraint is robot_b's (it must fix the plan)."""
    plan = team(("robot_a", [task("a1", "hold_position", "c")]), ("robot_b", [task("b1", "hold_position", "c")]))
    v = verdict_for(parking, plan, speed=1.0)
    assert s4(v) == [("custom", "robot_b", "b1", "c", None,
                      "robot_b: you stay at c until the end of your plan, but c is where task a1 of "
                      "robot_a ends, so that task cannot be done with the plan as it is. End somewhere "
                      "else, so that c is free when robot_a gets there.")]
    assert s4_cases(v) == ["cannot_be_done"]


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
    assert s4_cases(v) == ["from_start"]


def test_a_yielder_waiting_where_it_starts_waits_somewhere_else(parking):
    """robot_b stands at n, where it starts, until robot_c's c1 is done (14 s); robot_a gets to n
    at 9 s and stays. The ledger makes robot_b give way: it can wait for c1 somewhere else."""
    ledger = FairnessLedger()
    ledger.record_yield("robot_a", "earlier", 0.0, "robot_b")
    plan = team(("robot_a", [task("a1", "hold_position", "n")]),
                ("robot_b", [task("b1", "navigate", "s", depends_on=["c1"])]),
                ("robot_c", [task("c1", "navigate", "w")]))
    v = verdict_for(parking, plan, starts=PARKING_THREE, speed=1.0, ledger=ledger)
    assert s4(v) == [("forbid_node", "robot_b", "b1", "n", None,
                      "robot_b: do not wait at n, where you start; robot_a would stop there while you do. "
                      "Wait at another node until task b1 may start.")]
    assert s4_cases(v) == ["from_start"]


# ---------------- S-4 with a robot driving through the node ----------------

def test_two_robots_driving_through_one_node_at_once_take_turns(crossroads):
    v = verdict_for(crossroads, CROSSING, speed=1.0)
    assert (v.rule_ids, v.rationale) == ((R.NODE_OCCUPIED,), (
        "Plan rejected: robot_a and robot_b would drive through c at the same time. "
        "Propose a new plan that follows the constraints."))
    assert s4(v) == [("order_before", "robot_a", "b1", None, "a1",
                      "robot_a: start task a1 only after robot_b has finished task b1; both would drive "
                      "through c at the same time.")]
    assert s4_cases(v) == ["yielder_waits"]
    fixed = edited(CROSSING, lambda t: t["depends_on"].append("b1") if t["task_id"] == "a1" else None)
    assert verdict_for(crossroads, fixed, speed=1.0).verdict == APPROVE


def test_driving_through_where_a_robot_stops_for_a_while_waits_until_it_has_left(parking):
    """robot_b waits at c until robot_c is done (8 s); robot_a would drive through c at 5 s."""
    plan = team(("robot_a", [task("a1", "navigate", "e")]),
                ("robot_b", [task("b1", "navigate", "c"), task("b2", "navigate", "s", depends_on=["c2"])]),
                SLOW_C)
    v = verdict_for(parking, plan, starts=PARKING_THREE, speed=1.0)
    assert s4(v) == [("order_before", "robot_a", "b2", None, "a1",
                      "robot_a: start task a1 only after robot_b has finished task b2; robot_a would drive "
                      "through c while robot_b stands there.")]
    assert s4_cases(v) == ["yielder_waits"]


def test_a_keeper_waiting_where_the_yielder_drives_through_waits_somewhere_else(parking):
    """robot_b stands at c until robot_a's second task is done, so robot_a cannot wait for it
    before driving through c with its first: robot_b waits somewhere else."""
    plan = team(("robot_a", [task("a1", "navigate", "e"), task("a2", "navigate", "f")]),
                ("robot_b", [task("b1", "navigate", "c"), task("b2", "navigate", "s", depends_on=["a2"])]))
    v = verdict_for(parking, plan, speed=1.0)
    assert s4(v) == [("forbid_node", "robot_b", "b2", "c", None,
                      "robot_b: do not wait at c. robot_a has to drive through c with task a1, and your task "
                      "b2 cannot start before that is done, so robot_a cannot wait for you to leave. Wait at "
                      "another node until task b2 may start.")]
    assert s4_cases(v) == ["keeper_in_the_way"]


# robot_b parks at corridor_1 for good; robot_a has to drive through it.
KEEP_CLEAR_ROUND_3 = team(("robot_a", [task("a1", "navigate", "room_3")]),
                          ("robot_b", [task("b1", "hold_position", "corridor_1")]))
PARK_AFTER_PASS = team(("robot_a", [task("a1", "hold_position", "corridor_2")]),
                       ("robot_b", [task("b1", "hold_position", "corridor_1")]))


def test_a_keeper_parked_where_the_other_robot_drives_through_waits_for_it_if_that_works(samples_map):
    """robot_b gets to corridor_1 first and would stay; robot_a has to drive through it to
    corridor_2. robot_b can wait at east_n until robot_a has gone by: both keep their targets."""
    v = verdict_for(samples_map, PARK_AFTER_PASS)
    assert (v.rule_ids, v.rationale) == ((R.NODE_OCCUPIED,), (
        "Plan rejected: robot_a would drive through corridor_1 while robot_b stands there. "
        "Propose a new plan that follows the constraints."))
    assert s4(v) == [("order_before", "robot_b", "a1", None, "b1",
                      "robot_b: start task b1 only after robot_a has finished task a1; robot_a has to drive "
                      "through corridor_1, where you would stay until the end of your plan.")]
    assert s4_cases(v) == ["keeper_waits"]
    fixed = edited(PARK_AFTER_PASS, lambda t: t["depends_on"].append("a1") if t["task_id"] == "b1" else None)
    assert verdict_for(samples_map, fixed).verdict == APPROVE
    assert [t.target for a in fixed.assignments for t in a.tasks] == ["corridor_2", "corridor_1"]


def test_a_keeper_whose_waiting_would_meet_the_other_robot_elsewhere_ends_somewhere_else(samples_map):
    """p1_keep_clear_stop's round 3: robot_b parks at corridor_1. Waiting until robot_a has gone
    by removes the conflict at corridor_1, but robot_a then ends at room_3, which robot_b has to
    drive through from east_n: the whole plan is checked again, and that new meeting is found."""
    v = verdict_for(samples_map, KEEP_CLEAR_ROUND_3)
    assert s4(v) == [("forbid_node", "robot_b", "b1", "corridor_1", None,
                      "robot_b: do not end your plan at corridor_1. This changes your final stop, because "
                      "robot_a has to drive through corridor_1 (task a1) and you would stay there until the "
                      "end of your plan; waiting until robot_a has gone by does not help: you and robot_a "
                      "would then meet at room_3.")]
    assert s4_cases(v) == ["keeper_ends_elsewhere"]


def test_a_keeper_that_cannot_wait_without_a_cycle_ends_somewhere_else(samples_map):
    plan = edited(PARK_AFTER_PASS, lambda t: t["depends_on"].append("b1") if t["task_id"] == "a1" else None)
    v = verdict_for(samples_map, plan)
    [c] = v.constraints
    assert (c.kind, c.robot_id, c.task_id, c.node) == ("forbid_node", "robot_b", "b1", "corridor_1")
    assert c.text.endswith("waiting until robot_a has gone by does not help: the tasks would wait for each "
                           "other in a cycle.")
    assert v.cases == ("keeper_ends_elsewhere",)


# ---------------- the orderings of a verdict, together ----------------
# robot_b passes c at 4 s (b1 ends there, b2 drives on) and parks at e from 9 s; robot_a drives
# w -> c -> e -> f, at c at 5 s and through e at 10 s. robot_a gets to both later, so it yields
# in both. At c robot_b leaves: robot_a's task waits for b2. At e robot_b stays to the end:
# robot_b waits for robot_a's task that drives through e. Each ordering works on its own (each
# is verified), but together the tasks would wait for each other.

PASSES_C_PARKS_AT_E = ("robot_b", [task("b1", "navigate", "c"), task("b2", "hold_position", "e")])


def test_a_keepers_wait_that_closes_a_cycle_with_another_ordering_falls_back_to_forbid_node(parking):
    # One task of robot_a, a1, drives through both nodes: a1 after b2 and b2 after a1.
    plan = team(("robot_a", [task("a1", "navigate", "f")]), PASSES_C_PARKS_AT_E)
    v = verdict_for(parking, plan, speed=1.0)
    assert s4(v) == [
        ("order_before", "robot_a", "b2", None, "a1",
         "robot_a: start task a1 only after robot_b has finished task b2; both would drive through c at "
         "the same time."),
        ("forbid_node", "robot_b", "b2", "e", None,
         "robot_b: do not end your plan at e. This changes your final stop, because robot_a has to drive "
         "through e (task a1) and you would stay there until the end of your plan; waiting until robot_a "
         "has gone by does not help: together with the other orderings of this plan, the tasks would wait "
         "for each other in a cycle.")]
    assert s4_cases(v) == ["yielder_waits", "keeper_ends_elsewhere"]


def test_a_cycle_through_a_robots_own_task_order_is_found_too(parking):
    # robot_a reaches c with a1 and drives through e with a2: a1 after b2 and b2 after a2. The
    # depends_on alone have no cycle (TeamPlan accepts them), but a2 comes after a1.
    plan = team(("robot_a", [task("a1", "navigate", "c"), task("a2", "navigate", "f")]),
                PASSES_C_PARKS_AT_E)
    v = verdict_for(parking, plan, speed=1.0)
    assert [(c.kind, c.robot_id, c.task_id, c.node, c.value) for c in v.constraints] == [
        ("order_before", "robot_a", "b2", None, "a1"), ("forbid_node", "robot_b", "b2", "e", None)]
    assert v.cases == ("yielder_waits", "keeper_ends_elsewhere")


def test_a_cycle_left_without_the_keepers_waits_turns_the_last_orderings_into_custom():
    """Orderings that make a robot R-1 ranked lower wait cannot form a cycle by themselves, nor
    with each robot's task order; the plan's own depends_on can close one. robot_a yields to
    robot_b, robot_b to robot_c, and c1 already waits for a2. No compact map reaches this with
    each ordering verified on its own, so the last step of to_verdict is tested directly."""
    from fleet_checker.adapters.verdict import _labelled, _Proposal, _without_cycles
    plan = team(("robot_a", [task("a1", "navigate", "x"), task("a2", "navigate", "y")]),
                ("robot_b", [task("b1", "navigate", "x"), task("b2", "navigate", "y")]),
                ("robot_c", [task("c1", "navigate", "z", depends_on=["a2"])]))
    b_waits = Constraint(kind="order_before", rule_id=R.NODE_OCCUPIED, robot_id="robot_b", task_id="c1",
                         value="b1", text="robot_b: start task b1 only after robot_c has finished task c1.")
    a_waits = Constraint(kind="order_before", rule_id=R.CAPACITY_EXCEEDED, robot_id="robot_a", task_id="b2",
                         value="a1", text="robot_a: start task a1 only after robot_b has finished task b2.")
    proposals = [_Proposal(b_waits, case="yielder_waits"), _Proposal(a_waits, case="ordering")]
    kept, cut = _without_cycles(plan, proposals)
    assert kept == b_waits
    assert [p.case for p in _labelled(proposals, [kept, cut])] == ["yielder_waits", "cycle"]
    assert (cut.kind, cut.rule_id, cut.robot_id, cut.task_id) == ("custom", R.CAPACITY_EXCEEDED,
                                                                  "robot_a", "a1")
    assert cut.text == ("robot_a: start task a1 only after robot_b has finished task b2. This cannot be done "
                        "together with the plan's own depends_on and this verdict's other orderings: the tasks "
                        "would wait for each other in a cycle. Change the plan so that robot_a does not have to "
                        "wait for task b2.")


def test_orderings_that_work_together_are_left_alone(parking):
    # Without a cycle the orderings stay as they are: robot_a waits for robot_b at c.
    v = verdict_for(parking, team(("robot_a", [task("a1", "navigate", "e")]),
                                  ("robot_b", [task("b1", "navigate", "s")])), speed=1.0)
    assert [(c.kind, c.task_id, c.value) for c in v.constraints] == [("order_before", "b1", "a1")]
    assert v.cases == ("yielder_waits",)


# ---------------- one constraint per demand ----------------

def test_two_rules_asking_for_the_same_change_send_one_constraint(crossroads):
    # S-2 and S-4 both want a1 to wait for b1 at the crossing (see the crowding test above).
    v = verdict_for(crossroads, CROSSING, speed=1.0, people=[BY_C], crowding=LIMIT)
    assert [(c.kind, c.rule_id, c.task_id, c.value) for c in v.constraints] == [
        ("order_before", R.HUMAN_CROWDING, "b1", "a1")]


# ---------------- P-3 and compile errors -> custom ----------------

# ---------------- robots on an edge (team_state's at "a->b") ----------------

def test_a_keeper_leaving_on_the_edge_it_is_on_is_waited_for_until_its_first_task_is_done(samples_map):
    # robot_b is in the passage on pass_e -> corridor_1 and leaves it 0.8 s later, before its task b1
    # starts; with a 3 s margin robot_a's a1 still meets it there. No task event marks the end of
    # the edge, so robot_a waits for b1, the first one after it.
    plan = team(("robot_a", [task("a1", "navigate", "room_3")]), ("robot_b", [task("b1", "navigate", "corridor_2")]))
    v = verdict_for(samples_map, plan, {"robot_a": "west_s", "robot_b": "corridor_1"}, margin=3.0,
                    on_edges={"robot_b": OnEdge("pass_e", "corridor_1", (7.6, 0.0))})
    assert v.rule_ids == (R.CAPACITY_EXCEEDED,)
    assert fields(v, "kind", "robot_id", "task_id", "value") == [("order_before", "robot_a", "b1", "a1")]
    assert v.cases == ("ordering",)


def test_a_yielder_in_the_zone_on_the_edge_it_is_on_has_the_keeper_keep_clear(samples_map):
    # As in test_team_check: R-1 picks robot_b, which drives into the passage on its edge.
    ledger = FairnessLedger()
    ledger.record_yield("robot_a", "earlier", 0.0, "robot_b")
    plan = team(("robot_a", [task("a1", "navigate", "room_3")]), ("robot_b", [task("b1", "navigate", "corridor_2")]))
    v = verdict_for(samples_map, plan, {"robot_a": "room_2", "robot_b": "pass_e"}, margin=2.0, ledger=ledger,
                    on_edges={"robot_b": OnEdge("corridor_1", "pass_e", (8.6, 0.0))})
    assert fields(v, "kind", "robot_id", "task_id") == [("custom", "robot_a", "a1")]
    assert v.constraints[0].text == (
        "robot_a: robot_b will be in passage on the edge it is on now, so it cannot wait, and you would be "
        "there at the same time, but it holds 1 robot. Change the plan so that you are not in passage "
        "while robot_b is there.")


def test_a_yielder_reaching_the_node_on_the_edge_it_is_on_has_the_keeper_keep_clear(samples_map):
    plan = team(("robot_a", [task("a1", "navigate", "room_3")]), ("robot_b", [task("b1", "navigate", "room_2")]))
    v = verdict_for(samples_map, plan, {"robot_a": "corridor_2", "robot_b": "corridor_1"},
                    on_edges={"robot_b": OnEdge("room_3", "corridor_1", (11.0, 0.0))})
    assert fields(v, "kind", "robot_id", "task_id", "node") == [("custom", "robot_a", "a1", "corridor_1")]
    assert v.constraints[0].text == (
        "robot_a: robot_b gets to corridor_1 on the edge it is on now, so it cannot wait, and you would "
        "drive through it then. Change the plan so that you are not at corridor_1 while robot_b is there.")
    assert v.cases == ("on_its_edge",)


def test_a_keeper_that_stays_where_its_edge_ends_cannot_wait_and_ends_somewhere_else(samples_map):
    # robot_b has no task: it stays at corridor_1, where its edge ends, and robot_a drives through later.
    v = verdict_for(samples_map, team(("robot_a", [task("a1", "navigate", "room_3")])),
                    {"robot_a": "west_s", "robot_b": "corridor_1"},
                    on_edges={"robot_b": OnEdge("room_3", "corridor_1", (11.0, 0.0))})
    assert fields(v, "kind", "robot_id", "node") == [("forbid_node", "robot_b", "corridor_1")]
    assert v.constraints[0].text.endswith(
        "waiting until robot_a has gone by does not help: you get to corridor_1 on the edge you are on now.")
    assert v.cases == ("keeper_ends_elsewhere",)


def test_too_long_plan_is_custom(samples_map):
    v = verdict_for(samples_map, sample("valid_two_robots.json"), max_duration=30.0)
    [c] = v.constraints
    assert (c.kind, c.robot_id) == ("custom", "robot_b")
    assert "41 s" in c.text and "30 s" in c.text
    assert v.risk_score == pytest.approx(1 / 3)
    assert v.cases == ("duration",)


def test_compile_error_is_custom(samples_map):
    plan = edited(sample("valid_two_robots.json"),
                  lambda t: t.update(target="room_9") if t["task_id"] == "a2" else None)
    v = verdict_for(samples_map, plan)
    assert v.rule_ids == (R.UNKNOWN_TARGET,)
    [c] = v.constraints
    assert c.kind == "custom" and "room_9" in c.text
    assert v.risk_score == pytest.approx(1 / 3)
    assert v.cases == ("compile",)


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
    assert v.cases == ("unreadable_plan",)
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


# ---------------- positions unknown (the gate's start_source team_state) ----------------

def test_unknown_positions_reject_the_plan_unchecked():
    from fleet_checker.adapters.verdict import unknown_positions_verdict
    v = unknown_positions_verdict(((None, "no /mrs/team_state has been received yet"),))
    assert (v.verdict, v.rule_ids, v.standard_refs, v.risk_score) == (REJECT, (R.UNKNOWN_ROBOT,), (), 1 / 3)
    assert fields(v, "kind", "rule_id", "robot_id", "text") == [
        ("custom", R.UNKNOWN_ROBOT, None, "The gate does not know where the robots are: no /mrs/team_state "
                                          "has been received yet. Send the plan again once it does.")]
    assert v.rationale == ("Plan not checked: the gate does not know where the robots are. "
                           "Send the plan again once it does.")
    two = unknown_positions_verdict((("robot_a", "it is not in the latest /mrs/team_state"),
                                     ("robot_b", "its position has no time stamp")))
    assert fields(two, "robot_id") == [("robot_a",), ("robot_b",)]
    assert (v.cases, two.cases) == (("unknown_position",), ("unknown_position",) * 2)
    assert two.rationale.startswith("Plan not checked: the gate does not know where robot_a and robot_b are.")
