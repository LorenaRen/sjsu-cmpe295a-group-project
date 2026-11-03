"""scripted_repair: how the fake planner changes a TeamPlan for each constraint of a REJECT.

Hand-made plans and constraints on a small graph; no map file and no checker.
"""
from mrs_common import reasons as R
from mrs_common.plan_schema import Constraint, TeamPlan

from mrs_ethical_gate.dev_nodes.scripted_repair import CHANGED, NOT_APPLIED, NOTED, apply_constraints

#   x --1-- y --1-- z --2-- w
#           |
#          1.5
#           |
#           v
GRAPH = {
    "x": {"y": 1.0},
    "y": {"x": 1.0, "z": 1.0, "v": 1.5},
    "z": {"y": 1.0, "w": 2.0},
    "w": {"z": 2.0},
    "v": {"y": 1.5},
}


def task(task_id, action, target, depends_on=(), priority=1, justification=""):
    return {"task_id": task_id, "action": action, "target": target, "priority": priority,
            "justification": justification, "depends_on": list(depends_on)}


def team(*assignments) -> TeamPlan:
    return TeamPlan.model_validate(
        {"assignments": [{"robot_id": r, "tasks": tasks} for r, tasks in assignments]})


def order_before(robot, before, after):
    return Constraint(kind="order_before", rule_id=R.CAPACITY_EXCEEDED, robot_id=robot, task_id=before,
                      value=after, text=f"{robot}: start task {after} only after task {before}.")


def forbid(robot, task_id, node, rule=R.FINAL_STOP_IN_PASSTHROUGH):
    return Constraint(kind="forbid_node", rule_id=rule, robot_id=robot, task_id=task_id, node=node,
                      text=f"{robot}: do not stop at {node}.")


def avoid(robot, node):
    return Constraint(kind="avoid_node", rule_id=R.REGION_ACCESS_DENIED, robot_id=robot, node=node,
                      text=f"{robot}: do not drive through {node}.")


def results(repair):
    return [(o.result, o.change) for o in repair.outcomes]


# ---------------- order_before ----------------

def test_order_before_makes_the_later_task_wait():
    plan = team(("robot_a", [task("a1", "navigate", "z")]),
                ("robot_b", [task("b1", "navigate", "x"), task("b2", "hold_position", "v")]))
    r = apply_constraints(plan, [order_before("robot_b", "a1", "b2")], GRAPH)
    assert r.plan.task("b2").depends_on == ["a1"]
    assert r.changes == ("b2.depends_on += a1",)
    assert results(r) == [(CHANGED, "b2.depends_on += a1")]
    assert plan.task("b2").depends_on == []          # the plan given is left alone


def test_order_before_the_plan_already_has_is_not_applied():
    plan = team(("robot_a", [task("a1", "navigate", "z")]),
                ("robot_b", [task("b1", "hold_position", "v", depends_on=["a1"])]))
    r = apply_constraints(plan, [order_before("robot_b", "a1", "b1")], GRAPH)
    assert r.plan == plan and r.changes == ()
    [o] = r.outcomes
    assert o.result == NOT_APPLIED and "already" in o.why_not


def test_order_before_naming_an_unknown_task_is_not_applied():
    plan = team(("robot_a", [task("a1", "navigate", "z")]))
    r = apply_constraints(plan, [order_before("robot_b", "a1", "b9")], GRAPH)
    [o] = r.outcomes
    assert (o.result, r.plan) == (NOT_APPLIED, plan)
    assert "b9" in o.why_not


def test_order_before_that_would_close_a_cycle_is_not_applied():
    # The gate's orderings of one round cannot form a cycle (one ranking per round), so this
    # would show a checker bug; the fake planner keeps the first and refuses the second.
    plan = team(("robot_a", [task("a1", "navigate", "z")]), ("robot_b", [task("b1", "navigate", "x")]))
    r = apply_constraints(plan, [order_before("robot_a", "b1", "a1"), order_before("robot_b", "a1", "b1")],
                          GRAPH)
    assert (r.plan.task("a1").depends_on, r.plan.task("b1").depends_on) == (["b1"], [])
    assert [o.result for o in r.outcomes] == [CHANGED, NOT_APPLIED]
    assert "circular" in r.outcomes[1].why_not


# ---------------- forbid_node ----------------

def test_a_forbidden_target_moves_to_the_nearest_allowed_node():
    plan = team(("robot_b", [task("b1", "hold_position", "z", priority=3, justification="park here")]))
    r = apply_constraints(plan, [forbid("robot_b", "b1", "z")], GRAPH)
    assert r.plan.task("b1").target == "y"           # 1 m from z; w is 2 m
    assert results(r) == [(CHANGED, "b1.target z -> y")]
    # Only the target changes.
    b1 = r.plan.task("b1")
    assert (b1.action.value, b1.priority, b1.justification, b1.depends_on) == ("hold_position", 3, "park here", [])
    assert r.banned == {"robot_b": frozenset({"z"})}


def test_equally_near_nodes_are_chosen_by_name():
    plan = team(("robot_b", [task("b1", "hold_position", "y")]))
    r = apply_constraints(plan, [forbid("robot_b", "b1", "y")], GRAPH)
    assert r.plan.task("b1").target == "x"           # x and z are both 1 m from y


def test_every_node_of_the_zone_is_kept_out_and_the_task_moves_once():
    # P-1 names every node of the zone, here z and y. One retarget, past both; the other node
    # is only noted, so that no later retarget goes there.
    plan = team(("robot_b", [task("b1", "hold_position", "z")]))
    r = apply_constraints(plan, [forbid("robot_b", "b1", "z"), forbid("robot_b", "b1", "y")], GRAPH)
    assert r.plan.task("b1").target == "w"           # w and x are 2 m from z (x through y): by name
    assert results(r) == [(CHANGED, "b1.target z -> w"), (NOTED, None)]
    assert r.banned == {"robot_b": frozenset({"y", "z"})}


def test_a_robot_is_never_sent_back_to_a_node_refused_in_an_earlier_round():
    plan = team(("robot_b", [task("b1", "hold_position", "x")]))
    first = apply_constraints(plan, [forbid("robot_b", "b1", "x")], GRAPH)
    assert first.plan.task("b1").target == "y"
    # From y, x and z are equally near and x comes first by name, but x was refused in round 1.
    second = apply_constraints(first.plan, [avoid("robot_b", "y")], GRAPH, first.banned)
    assert second.plan.task("b1").target == "z"
    assert second.banned == {"robot_b": frozenset({"x", "y"})}


def test_a_robot_that_waits_at_the_node_before_its_task_waits_somewhere_else():
    # S-4: robot_a reaches z with a1, then would stand there waiting for b1 before a2 leaves.
    # z is not where a2 goes, so a navigate to the nearest other node goes in before a2, and
    # robot_a waits there instead. a2 itself is unchanged.
    plan = team(("robot_a", [task("a1", "navigate", "z"), task("a2", "navigate", "w", depends_on=["b1"])]),
                ("robot_b", [task("b1", "navigate", "v")]))
    r = apply_constraints(plan, [forbid("robot_a", "a2", "z", rule=R.NODE_OCCUPIED)], GRAPH)
    assert [(t.task_id, t.action.value, t.target, t.depends_on) for t in r.plan.tasks_of("robot_a")] == [
        ("a1", "navigate", "z", []), ("a2_wait", "navigate", "y", []), ("a2", "navigate", "w", ["b1"])]
    assert results(r) == [(CHANGED, "a2_wait: navigate y before a2, to wait there instead of at z")]
    assert r.plan.task("a2_wait").priority == r.plan.task("a2").priority


def test_a_waiting_point_and_a_retarget_in_one_reject():
    # The inserted task is new to this REJECT: avoid_node looks only at the tasks proposed.
    plan = team(("robot_a", [task("a1", "navigate", "z"), task("a2", "navigate", "w", depends_on=["b1"])]),
                ("robot_b", [task("b1", "navigate", "v")]))
    r = apply_constraints(plan, [forbid("robot_a", "a2", "z", rule=R.NODE_OCCUPIED), avoid("robot_a", "w")],
                          GRAPH)
    assert [(t.task_id, t.target) for t in r.plan.tasks_of("robot_a")] == [("a1", "z"), ("a2_wait", "y"),
                                                                             ("a2", "y")]
    assert [o.result for o in r.outcomes] == [CHANGED, CHANGED]


def test_an_inserted_task_gets_an_id_no_other_task_has():
    plan = team(("robot_a", [task("a1", "navigate", "z"), task("a2", "navigate", "w", depends_on=["a2_wait"])]),
                ("robot_b", [task("a2_wait", "navigate", "v")]))
    r = apply_constraints(plan, [forbid("robot_a", "a2", "z", rule=R.NODE_OCCUPIED)], GRAPH)
    assert [t.task_id for t in r.plan.tasks_of("robot_a")] == ["a1", "a2_wait2", "a2"]


def test_forbid_node_without_a_task_is_not_applied():
    # A robot with no task stands at its start node: there is no task to change.
    plan = team(("robot_a", [task("a1", "navigate", "w")]), ("robot_b", []))
    c = Constraint(kind="forbid_node", rule_id=R.FINAL_STOP_IN_PASSTHROUGH, robot_id="robot_b", node="z",
                   text="robot_b: do not stop at z.")
    r = apply_constraints(plan, [c], GRAPH)
    [o] = r.outcomes
    assert (o.result, r.plan) == (NOT_APPLIED, plan)
    assert "no task" in o.why_not


def test_forbid_node_where_the_task_does_not_stop_is_not_applied():
    plan = team(("robot_b", [task("b1", "hold_position", "w")]))
    r = apply_constraints(plan, [forbid("robot_b", "b1", "z")], GRAPH)
    [o] = r.outcomes
    assert (o.result, r.plan) == (NOT_APPLIED, plan)
    assert "does not stop at z" in o.why_not


def test_forbid_node_for_a_task_of_another_robot_is_not_applied():
    plan = team(("robot_a", [task("a1", "navigate", "w")]), ("robot_b", [task("b1", "hold_position", "z")]))
    r = apply_constraints(plan, [forbid("robot_a", "b1", "z")], GRAPH)
    [o] = r.outcomes
    assert (o.result, r.plan) == (NOT_APPLIED, plan)
    assert "robot_b's" in o.why_not


def test_no_node_left_to_go_to_is_not_applied():
    tiny = {"p": {"q": 1.0}, "q": {"p": 1.0}}
    plan = team(("robot_b", [task("b1", "hold_position", "p")]))
    r = apply_constraints(plan, [forbid("robot_b", "b1", "p"), forbid("robot_b", "b1", "q")], tiny)
    assert r.plan == plan
    assert r.outcomes[0].result == NOT_APPLIED and "no node left" in r.outcomes[0].why_not


# ---------------- avoid_node ----------------

def test_an_avoided_target_moves_and_the_rest_of_the_zone_is_noted():
    # P-2 names every node of the zone; only the one the plan goes to has to change now.
    plan = team(("robot_a", [task("a1", "navigate", "x"), task("a2", "hold_position", "w", depends_on=["a1"])]))
    r = apply_constraints(plan, [avoid("robot_a", "x"), avoid("robot_a", "y")], GRAPH)
    assert r.plan.task("a1").target == "z"           # through y, which is avoided but may be crossed
    assert results(r) == [(CHANGED, "a1.target x -> z"), (NOTED, None)]


def test_avoided_nodes_that_no_task_goes_to_are_on_the_route():
    # The gate rejects robot_a for driving through the zone, but none of its tasks goes there:
    # the route crosses it, and a TeamPlan cannot choose the route.
    plan = team(("robot_a", [task("a1", "hold_position", "w")]))
    r = apply_constraints(plan, [avoid("robot_a", "y"), avoid("robot_a", "z")], GRAPH)
    assert r.plan == plan and r.changes == ()
    assert [o.result for o in r.outcomes] == [NOT_APPLIED, NOT_APPLIED]
    assert "route" in r.outcomes[0].why_not
    assert r.banned == {"robot_a": frozenset({"y", "z"})}


# ---------------- custom and other kinds ----------------

def test_custom_constraints_are_recorded_and_not_applied():
    plan = team(("robot_a", [task("a1", "hold_position", "z")]), ("robot_b", [task("b1", "hold_position", "z")]))
    c = Constraint(kind="custom", rule_id=R.NODE_OCCUPIED, robot_id="robot_a", task_id="a1", node="z",
                   text="robot_a: task a1 cannot be done with the plan as it is.")
    r = apply_constraints(plan, [c], GRAPH)
    assert r.plan == plan and r.changes == ()
    [o] = r.outcomes
    assert o.result == NOT_APPLIED and "text" in o.why_not
    assert o.as_record()["text"] == c.text


def test_kinds_the_gate_does_not_send_are_not_applied():
    plan = team(("robot_a", [task("a1", "hold_position", "z")]))
    c = Constraint(kind="replace_target", rule_id=R.FINAL_STOP_IN_PASSTHROUGH, robot_id="robot_a",
                   task_id="a1", value="w", text="robot_a: park at w instead.")
    [o] = apply_constraints(plan, [c], GRAPH).outcomes
    assert o.result == NOT_APPLIED and "replace_target" in o.why_not


def test_outcomes_follow_the_order_of_the_constraints():
    plan = team(("robot_a", [task("a1", "navigate", "x")]),
                ("robot_b", [task("b1", "navigate", "v"), task("b2", "hold_position", "z")]))
    constraints = [avoid("robot_a", "x"), order_before("robot_b", "a1", "b2"),
                   Constraint(kind="custom", rule_id=R.MAX_DURATION_EXCEEDED, text="make the plan shorter.")]
    r = apply_constraints(plan, constraints, GRAPH)
    assert [o.constraint for o in r.outcomes] == constraints
    assert [o.result for o in r.outcomes] == [CHANGED, CHANGED, NOT_APPLIED]
    assert r.changes == ("a1.target x -> y", "b2.depends_on += a1")


def test_the_record_of_an_outcome_leaves_out_empty_fields():
    plan = team(("robot_a", [task("a1", "navigate", "z")]),
                ("robot_b", [task("b1", "navigate", "x"), task("b2", "hold_position", "v")]))
    c = order_before("robot_b", "a1", "b2")
    [o] = apply_constraints(plan, [c], GRAPH).outcomes
    assert o.as_record() == {"kind": "order_before", "rule_id": R.CAPACITY_EXCEEDED, "robot_id": "robot_b",
                             "task_id": "a1", "value": "b2", "text": c.text, "result": CHANGED,
                             "change": "b2.depends_on += a1"}
