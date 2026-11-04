"""replan_loop: what the fake planner sends, when it stops, and what it records.

First with verdicts written out by hand (no checker), then against the gate's own session on
samples_world (conftest.run_with_gate), the way the two nodes run, without ROS.
"""
import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R
from mrs_common.plan_schema import Constraint, TeamPlan, constraints_to_json, hold_all_plan

from mrs_ethical_gate.dev_nodes.replan_loop import (APPROVE, MODIFY, REJECT, FakePlanner, Finished,
                                                    GateVerdict, Proposal)
from mrs_ethical_gate.dev_nodes.scripted_repair import CHANGED, NOT_APPLIED, NOTED

#   x --1-- y --1-- z --2-- w
#           |
#          1.5
#           |
#           v
GRAPH = {"x": {"y": 1.0}, "y": {"x": 1.0, "z": 1.0, "v": 1.5}, "z": {"y": 1.0, "w": 2.0},
         "w": {"z": 2.0}, "v": {"y": 1.5}}
STARTS = {"robot_a": "x", "robot_b": "w", "robot_c": "v"}   # the whole fleet; robot_c has no task


def task(task_id, action, target, depends_on=()):
    return {"task_id": task_id, "action": action, "target": target, "priority": 1,
            "depends_on": list(depends_on)}


def team(*assignments) -> TeamPlan:
    return TeamPlan.model_validate(
        {"assignments": [{"robot_id": r, "tasks": tasks} for r, tasks in assignments]})


PLAN = team(("robot_a", [task("a1", "navigate", "z")]),
            ("robot_b", [task("b1", "navigate", "x"), task("b2", "hold_position", "v")]))
B2_AFTER_A1 = Constraint(kind="order_before", rule_id=R.CAPACITY_EXCEEDED, robot_id="robot_b", task_id="a1",
                         value="b2", text="robot_b: start task b2 only after robot_a has finished task a1.")
CANNOT = Constraint(kind="custom", rule_id=R.NODE_OCCUPIED, robot_id="robot_a", task_id="a1", node="z",
                    text="robot_a: task a1 cannot be done with the plan as it is.")


def fake(plan=PLAN, max_attempts=5):
    return FakePlanner(plan, graph=GRAPH, start_nodes=STARTS, max_attempts=max_attempts,
                       transaction_id="txn-1", episode_id="ep-1",
                       context={"scenario": "test", "map": "graph"})


def reject(attempt, *constraints, rule_ids=(R.CAPACITY_EXCEEDED,), txn="txn-1"):
    return GateVerdict(transaction_id=txn, attempt=attempt, verdict=REJECT, rule_ids=tuple(rule_ids),
                       risk_score=1.0, rationale="Plan rejected: something.",
                       constraints_json=constraints_to_json(constraints), latency_ms=0.4)


def approve(attempt, txn="txn-1"):
    return GateVerdict(transaction_id=txn, attempt=attempt, verdict=APPROVE,
                       rationale="Plan approved: no rule violations found.")


# ---------------- the loop ----------------

def test_attempt_0_is_the_scenario_plan():
    p = fake().first()
    assert (p.attempt, p.plan, p.changes, p.planning_ms) == (0, PLAN, (), 0.0)
    assert p.digest == "fake planner: the scenario's plan"


def test_a_reject_gives_the_next_attempt_with_the_constraints_applied():
    f = fake()
    f.first()
    p = f.on_verdict(reject(0, B2_AFTER_A1))
    assert isinstance(p, Proposal) and p.attempt == 1
    assert p.plan.task("b2").depends_on == ["a1"]
    assert p.changes == ("b2.depends_on += a1",)
    assert p.digest == "fake planner: b2.depends_on += a1"
    assert p.planning_ms >= 0.0
    assert f.transaction_id == "txn-1" and f.awaiting == 1


def test_approve_ends_the_loop():
    f = fake()
    f.first()
    f.on_verdict(reject(0, B2_AFTER_A1))
    done = f.on_verdict(approve(1))
    assert done == Finished("approved")
    assert f.finished == done and f.awaiting is None
    s = f.summary()
    assert (s["outcome"], s["rounds"], s["passed_in_round"], s["fallback"]) == ("approved", 2, 2, None)


def test_modify_ends_the_loop_too():
    # The gate sends no MODIFY yet; by rules_v1.md's design it publishes the modified plan itself.
    f = fake()
    f.first()
    v = GateVerdict(transaction_id="txn-1", attempt=0, verdict=MODIFY)
    assert f.on_verdict(v) == Finished("modified")
    assert f.summary()["passed_in_round"] == 1


def test_verdicts_for_another_plan_or_attempt_are_ignored():
    f = fake()
    f.first()
    assert f.on_verdict(approve(0, txn="txn-2")) is None        # someone else's plan
    assert f.on_verdict(approve(1)) is None                     # not the attempt it waits for
    assert f.rounds == [] and f.finished is None
    assert f.on_verdict(approve(0)) == Finished("approved")
    assert f.on_verdict(approve(0)) is None                     # a repeat, after the end


def test_an_unknown_verdict_is_an_error():
    f = fake()
    f.first()
    with pytest.raises(ValueError, match="MAYBE"):
        f.on_verdict(GateVerdict(transaction_id="txn-1", attempt=0, verdict="MAYBE"))


def test_a_reject_with_nothing_to_apply_falls_back_at_once():
    f = fake()
    f.first()
    done = f.on_verdict(reject(0, CANNOT, rule_ids=(R.NODE_OCCUPIED,)))
    assert done.outcome == "stuck"
    # Every robot of the fleet holds where it starts, robot_c too, though the plan leaves it out.
    assert done.fallback == hold_all_plan(["robot_a", "robot_b", "robot_c"], STARTS, R.PLANNING_EXHAUSTED)
    assert done.fallback_attempt == 0
    assert done.event_detail == {"reason": R.PLANNING_EXHAUSTED, "transaction_id": "txn-1", "attempts": 1,
                                 "max_attempts": 5,
                                 "stopped_early": "no constraint of the REJECT could be applied"}
    assert f.summary()["fallback"] == {"attempt": 0, "reason": R.PLANNING_EXHAUSTED,
                                       "hold": {"robot_a": "x", "robot_b": "w", "robot_c": "v"}}


def test_running_out_of_attempts_falls_back():
    f = fake(max_attempts=2)
    f.first()
    assert isinstance(f.on_verdict(reject(0, B2_AFTER_A1)), Proposal)
    b1_after_a1 = B2_AFTER_A1.model_copy(update={"value": "b1"})
    done = f.on_verdict(reject(1, b1_after_a1))
    assert (done.outcome, done.fallback_attempt) == ("exhausted", 1)
    assert done.fallback == hold_all_plan(list(STARTS), STARTS, R.PLANNING_EXHAUSTED)
    assert done.event_detail == {"reason": R.PLANNING_EXHAUSTED, "transaction_id": "txn-1", "attempts": 2,
                                 "max_attempts": 2}
    last = f.rounds[-1]
    assert last["changes"] == ["b1.depends_on += a1"]           # what it would have sent
    assert last["then"].startswith("exhausted")


def test_one_attempt_falls_back_after_the_first_reject():
    f = fake(max_attempts=1)
    f.first()
    assert f.on_verdict(reject(0, B2_AFTER_A1)).outcome == "exhausted"


def test_max_attempts_must_be_at_least_1():
    with pytest.raises(ValueError, match="max_attempts"):
        fake(max_attempts=0)


def test_no_verdict_ends_the_loop_without_a_fallback():
    f = fake()
    f.first()
    assert f.timed_out() == Finished("no_verdict")
    assert f.summary()["outcome"] == "no_verdict"


def test_refused_nodes_are_remembered_from_one_reject_to_the_next():
    plan = team(("robot_b", [task("b1", "hold_position", "x")]))
    f = fake(plan)
    f.first()
    forbid_x = Constraint(kind="forbid_node", rule_id=R.FINAL_STOP_IN_PASSTHROUGH, robot_id="robot_b",
                          task_id="b1", node="x", text="robot_b: do not stop at x.")
    avoid_y = Constraint(kind="avoid_node", rule_id=R.REGION_ACCESS_DENIED, robot_id="robot_b", node="y",
                         text="robot_b: do not drive through y.")
    assert f.on_verdict(reject(0, forbid_x)).plan.task("b1").target == "y"
    # From y, x and z are equally near and x comes first by name, but x was refused before.
    assert f.on_verdict(reject(1, avoid_y)).plan.task("b1").target == "z"
    assert f.summary()["targets_changed"] == {"b1": ["x", "y", "z"]}


# ---------------- the record ----------------

def test_each_round_records_the_verdict_and_what_each_constraint_did():
    f = fake()
    f.first()
    f.on_verdict(reject(0, B2_AFTER_A1, CANNOT, rule_ids=(R.CAPACITY_EXCEEDED, R.NODE_OCCUPIED)))
    f.on_verdict(approve(1))
    first, second = f.rounds
    assert first == {
        "round": 1, "attempt": 0, "verdict": REJECT, "rule_ids": [R.CAPACITY_EXCEEDED, R.NODE_OCCUPIED],
        "risk_score": 1.0, "gate_latency_ms": 0.4, "rationale": "Plan rejected: something.",
        "constraints": [
            {"kind": "order_before", "rule_id": R.CAPACITY_EXCEEDED, "robot_id": "robot_b", "task_id": "a1",
             "value": "b2", "text": B2_AFTER_A1.text, "result": CHANGED, "change": "b2.depends_on += a1"},
            {"kind": "custom", "rule_id": R.NODE_OCCUPIED, "robot_id": "robot_a", "task_id": "a1", "node": "z",
             "text": CANNOT.text, "result": NOT_APPLIED,
             "why_not": "a custom constraint is text for an LLM; the fake planner does not read text"}],
        "changes": ["b2.depends_on += a1"], "then": "attempt 1 sent"}
    assert (second["round"], second["verdict"], second["constraints"], second["then"]) == (2, APPROVE, [], "approved")


def test_the_summary_counts_constraints_by_kind_and_rule():
    f = fake()
    f.first()
    f.on_verdict(reject(0, B2_AFTER_A1, CANNOT, rule_ids=(R.CAPACITY_EXCEEDED, R.NODE_OCCUPIED)))
    f.on_verdict(approve(1))
    s = f.summary()
    assert s["constraints"] == [
        {"kind": "custom", "rule_id": R.NODE_OCCUPIED, "received": 1, CHANGED: 0, NOTED: 0, NOT_APPLIED: 1},
        {"kind": "order_before", "rule_id": R.CAPACITY_EXCEEDED, "received": 1, CHANGED: 1, NOTED: 0,
         NOT_APPLIED: 0}]
    assert s["not_applied"] == [{"round": 1, "kind": "custom", "rule_id": R.NODE_OCCUPIED, "robot_id": "robot_a",
                                 "why_not": "a custom constraint is text for an LLM; the fake planner does not "
                                            "read text", "text": CANNOT.text}]
    assert (s["targets_changed"], s["tasks_inserted"]) == ({}, [])
    assert {k: s[k] for k in ("scenario", "map", "fleet", "max_attempts", "transaction_id", "episode_id")} == {
        "scenario": "test", "map": "graph", "fleet": ["robot_a", "robot_b", "robot_c"], "max_attempts": 5,
        "transaction_id": "txn-1", "episode_id": "ep-1"}
    assert s["round_log"] == f.rounds


def test_the_summary_lists_inserted_tasks():
    plan = team(("robot_a", [task("a1", "navigate", "z"), task("a2", "navigate", "w", depends_on=["b1"])]),
                ("robot_b", [task("b1", "navigate", "v")]))
    f = fake(plan)
    f.first()
    wait_elsewhere = Constraint(kind="forbid_node", rule_id=R.NODE_OCCUPIED, robot_id="robot_a", task_id="a2",
                                node="z", text="robot_a: do not wait at z.")
    f.on_verdict(reject(0, wait_elsewhere, rule_ids=(R.NODE_OCCUPIED,)))
    assert f.summary()["tasks_inserted"] == ["a2_wait: navigate y before a2, to wait there instead of at z"]


def test_the_summary_in_words():
    f = fake()
    f.first()
    f.on_verdict(reject(0, B2_AFTER_A1, CANNOT, rule_ids=(R.CAPACITY_EXCEEDED, R.NODE_OCCUPIED)))
    f.on_verdict(approve(1))
    assert f.summary_text() == [
        "fake planner: scenario test on map graph, fleet robot_a, robot_b, robot_c, at most 5 attempts",
        "round 1 (attempt 0): REJECT capacity_exceeded, node_occupied",
        "  order_before for robot_b (capacity_exceeded): changed (b2.depends_on += a1)",
        "  custom z for robot_a (node_occupied): not applied: a custom constraint is text for an LLM; the fake "
        "planner does not read text",
        f"    {CANNOT.text}",
        "round 2 (attempt 1): APPROVE",
        "outcome: approved in round 2, no task target changed",
        "constraints: custom node_occupied 1 received, 1 not applied; "
        "order_before capacity_exceeded 1 received, 1 changed",
    ]


def test_the_summary_in_words_after_a_fallback():
    f = fake()
    f.first()
    f.on_verdict(reject(0, CANNOT, rule_ids=(R.NODE_OCCUPIED,)))
    assert f.summary_text()[-2:] == [
        "outcome: stuck in round 1: no constraint of the REJECT could be applied; FALLBACK: every robot "
        "holds where it starts (robot_a at x, robot_b at w, robot_c at v)",
        "constraints: custom node_occupied 1 received, 1 not applied",
    ]


# ---------------- against the gate's session, on samples_world ----------------

def without_dependencies(plan: TeamPlan) -> TeamPlan:
    data = plan.model_dump(mode="json")
    for a in data["assignments"]:
        for t in a["tasks"]:
            t["depends_on"] = []
    return TeamPlan.model_validate(data)


S1_NO_ORDER = without_dependencies(plan_from_json(load_sample("valid_two_robots.json")))
KEEP_CLEAR_STOP = plan_from_json(load_sample("keep_clear_stop.json"))
RESTRICTED_TARGET = plan_from_json(load_sample("restricted_target.json"))
BOTH_HOLD_ROOM_3 = team(("robot_a", [task("a1", "hold_position", "room_3")]),
                        ("robot_b", [task("b1", "hold_position", "room_3")]))
PARK_AFTER_PASS = team(("robot_a", [task("a1", "hold_position", "corridor_2")]),
                       ("robot_b", [task("b1", "hold_position", "corridor_1")]))
B_PARKS_IN_PASSAGE = team(("robot_a", [task("a1", "navigate", "corridor_1", depends_on=["b1"])]),
                          ("robot_b", [task("b1", "hold_position", "pass_e")]))


def rule_ids(f):
    return [r["rule_ids"] for r in f.rounds]


def results(f, round_number):
    return [(c["kind"], c["result"]) for c in f.rounds[round_number - 1]["constraints"]]


def test_the_s1_conflict_is_fixed_in_round_2(run_with_gate):
    f, done = run_with_gate(S1_NO_ORDER)
    assert done == Finished("approved")
    assert rule_ids(f) == [[R.CAPACITY_EXCEEDED], []]
    [c] = f.rounds[0]["constraints"]
    assert (c["kind"], c["robot_id"], c["task_id"], c["value"], c["result"]) == (
        "order_before", "robot_b", "a1", "b2", CHANGED)
    s = f.summary()
    assert (s["passed_in_round"], s["targets_changed"]) == (2, {})


def test_keep_clear_stop_takes_four_rounds_and_parks_robot_b_out_of_the_way(run_with_gate):
    f, done = run_with_gate(KEEP_CLEAR_STOP)
    assert done == Finished("approved")
    assert rule_ids(f) == [[R.FINAL_STOP_IN_PASSTHROUGH], [R.REGION_ACCESS_DENIED], [R.NODE_OCCUPIED], []]
    # door_A's nearest node is office_e, in the restricted office: the fake planner reads no zone
    # type, so it takes the gate's second REJECT to learn that. corridor_1 is next, but robot_a has
    # to drive through it on its way to room_3: robot_b waiting until robot_a has gone by would
    # make them meet at room_3 instead (the gate checks that on the whole plan), so robot_b is told
    # not to end there, and parks at corridor_2, a dead end nobody drives through.
    assert results(f, 3) == [("forbid_node", CHANGED)]
    assert f.rounds[2]["constraints"][0]["robot_id"] == "robot_b"
    assert f.summary()["targets_changed"] == {"b1": ["door_A", "office_e", "corridor_1", "corridor_2"]}


def test_a_robot_that_parks_where_the_other_drives_through_waits_and_both_keep_their_targets(run_with_gate):
    # robot_b would park at corridor_1 first, and robot_a has to drive through it to corridor_2.
    # robot_b can wait at east_n until robot_a has gone by: an order_before for robot_b, which
    # the fake planner applies, and no target changes.
    f, done = run_with_gate(PARK_AFTER_PASS)
    assert done == Finished("approved")
    assert rule_ids(f) == [[R.NODE_OCCUPIED], []]
    [c] = f.rounds[0]["constraints"]
    assert (c["kind"], c["robot_id"], c["result"], c["change"]) == ("order_before", "robot_b", CHANGED,
                                                                    "b1.depends_on += a1")
    assert f.summary()["targets_changed"] == {}


def test_restricted_target_takes_three_rounds(run_with_gate):
    f, done = run_with_gate(RESTRICTED_TARGET)
    assert done == Finished("approved")
    assert rule_ids(f) == [[R.REGION_ACCESS_DENIED], [R.CAPACITY_EXCEEDED], []]
    assert results(f, 1) == [("avoid_node", CHANGED), ("avoid_node", NOTED), ("avoid_node", NOTED)]
    assert f.summary()["targets_changed"] == {"a1": ["office_1", "door_A"]}
    assert results(f, 2) == [("order_before", CHANGED)]


def test_two_robots_holding_at_one_node_cannot_be_fixed(run_with_gate):
    f, done = run_with_gate(BOTH_HOLD_ROOM_3)
    assert done.outcome == "stuck"
    assert results(f, 1) == [("custom", NOT_APPLIED)]
    assert f.summary()["fallback"]["hold"] == {"robot_a": "west_s", "robot_b": "east_n"}


def test_a_fix_for_one_rule_can_lead_to_a_conflict_under_another(run_with_gate):
    # Round 1: robot_b may not park in the passage (P-1), and robot_a cannot just wait for it
    # (S-1, custom). Moving robot_b to corridor_1 puts it on robot_a's target (S-4, custom).
    f, done = run_with_gate(B_PARKS_IN_PASSAGE)
    assert done.outcome == "stuck"
    assert rule_ids(f) == [[R.CAPACITY_EXCEEDED, R.FINAL_STOP_IN_PASSTHROUGH], [R.NODE_OCCUPIED]]
    assert sorted(results(f, 1)) == [("custom", NOT_APPLIED), ("forbid_node", CHANGED), ("forbid_node", NOTED)]
    assert f.summary()["targets_changed"] == {"b1": ["pass_e", "corridor_1"]}


def test_one_attempt_is_not_enough_for_the_s1_conflict(run_with_gate):
    f, done = run_with_gate(S1_NO_ORDER, max_attempts=1)
    assert (done.outcome, done.fallback_attempt) == ("exhausted", 0)


@pytest.mark.parametrize("plan", [S1_NO_ORDER, KEEP_CLEAR_STOP, RESTRICTED_TARGET, BOTH_HOLD_ROOM_3,
                                  B_PARKS_IN_PASSAGE, PARK_AFTER_PASS])
def test_every_order_before_of_a_round_can_be_applied(run_with_gate, plan):
    # rules_v1.md: the orderings the gate sends in one round cannot form a cycle. The fake
    # planner applies them independently of the checker, so this checks that claim.
    f, _ = run_with_gate(plan)
    refused = [c for r in f.rounds for c in r["constraints"] if c["kind"] == "order_before"
               and c["result"] != CHANGED]
    assert refused == []
