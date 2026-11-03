"""S-4 node occupancy: two robots may not stand at the same node at once.

stop_claims (plan.py) says where and when a robot stands still at a node; check_node_occupancy
(cross_plan.py) sweeps each node as a resource of capacity 1. Plans are written by hand with
the end_node the compiler would fill in.
"""
import math

import pytest

from fleet_checker.cross_plan import (
    FairnessLedger, assign_yield, check_capacity_violations, check_cross_plan_conflicts,
    check_node_occupancy, node_conflicts_to_violations,
)
from fleet_checker.plan import Plan, Region, RegionKind, StepRef, WorldMap, stop_claims

# A hall with room for everyone, and a one-robot aisle off it.
WORLD = WorldMap(regions={
    "hall": Region(id="hall", kind=RegionKind.OPEN_AREA, capacity=100, center=(0, 0),
                   neighbors=frozenset({"aisle"})),
    "aisle": Region(id="aisle", kind=RegionKind.AISLE, capacity=1, center=(20, 0),
                    neighbors=frozenset({"hall"})),
})


def plan_of(robot_id, *steps, t0=0.0):
    """steps: (action, seconds, end_node) or (action, seconds, end_node, region), back to back."""
    rows, t = [], t0
    for action, seconds, end_node, *region in steps:
        rows.append({"action": action, "region": region[0] if region else "hall",
                     "t_start": t, "t_end": t + seconds, "end_node": end_node})
        t += seconds
    return Plan.model_validate({"plan_id": f"{robot_id}-p", "robot_id": robot_id,
                                "task_type": "delivery", "steps": rows})


def stops(plan, **kwargs):
    return [(c.resource_id, c.step_index, c.start, c.end) for c in stop_claims(plan, **kwargs)]


def spans(conflicts):
    return [(c.resource_id, c.t_start, c.t_end, [(x.robot_id, x.step_index) for x in c.claims])
            for c in conflicts]


# ---------------- where and when a robot stands still ----------------

class TestStopClaims:

    def test_standing_steps_and_the_node_the_plan_ends_at(self):
        # waits at n1 for 10 s, drives 5 s to n2 and stays there
        plan = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        assert stops(plan) == [("n1", 0, 0, 10), ("n2", 1, 15, math.inf)]

    def test_without_hold_final_the_plan_end_is_not_a_stop(self):
        plan = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        assert stops(plan, hold_final=False) == [("n1", 0, 0, 10)]

    def test_a_plan_that_ends_standing_holds_that_node(self):
        assert stops(plan_of("r1", ("navigate", 4, "n1"), ("wait", 1, "n1"))) == [("n1", 1, 4, math.inf)]

    def test_driving_through_a_node_is_not_a_stop(self):
        plan = plan_of("r1", ("navigate", 5, "n1"), ("navigate", 5, "n2"), ("navigate", 5, None))
        assert stops(plan) == []                       # and the plan ends at no node

    def test_margin_widens_both_ends(self):
        plan = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        assert stops(plan, margin=0.5) == [("n1", 0, -0.5, 10.5), ("n2", 1, 14.5, math.inf)]

    def test_plans_without_end_nodes_have_no_stops(self):
        assert stops(plan_of("r1", ("wait", 10, None), ("navigate", 5, None))) == []

    def test_negative_margin_is_refused(self):
        with pytest.raises(ValueError, match="margin"):
            list(stop_claims(plan_of("r1", ("wait", 1, "n1")), margin=-0.1))


# ---------------- two robots at one node ----------------

class TestCheckNodeOccupancy:

    def test_two_robots_ending_at_one_node(self):
        r1 = plan_of("r1", ("navigate", 5, "c"))
        r2 = plan_of("r2", ("navigate", 4, "c"))
        [c] = check_node_occupancy([r1, r2], WORLD)
        assert spans([c]) == [("c", 5, math.inf, [("r1", 0), ("r2", 0)])]
        assert (c.capacity, c.is_edge_swap) == (1, False)

    def test_a_robot_arriving_to_stop_where_another_is_waiting(self):
        r1 = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))   # at n1 until 10 s
        r2 = plan_of("r2", ("navigate", 5, "n1"))                         # stops at n1 from 5 s
        assert spans(check_node_occupancy([r1, r2], WORLD)) == [("n1", 5, 10, [("r1", 0), ("r2", 0)])]

    def test_driving_through_where_another_robot_stands_is_not_a_conflict(self):
        r1 = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        r2 = plan_of("r2", ("navigate", 5, "n1"), ("navigate", 5, "n3"))  # passes n1 at 5 s
        assert check_node_occupancy([r1, r2], WORLD) == []

    def test_a_handover_is_clean_without_margin_and_too_tight_with_it(self):
        r1 = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))   # leaves n1 at 10 s
        r2 = plan_of("r2", ("navigate", 10, "n1"))                        # arrives at n1 at 10 s
        assert check_node_occupancy([r1, r2], WORLD) == []
        assert spans(check_node_occupancy([r1, r2], WORLD, margin=0.5)) == [
            ("n1", 9.5, 10.5, [("r1", 0), ("r2", 0)])]

    def test_stops_in_a_one_robot_zone_are_left_to_s1(self):
        r1 = plan_of("r1", ("navigate", 5, "a1", "aisle"))
        r2 = plan_of("r2", ("navigate", 4, "a1", "aisle"))
        assert check_node_occupancy([r1, r2], WORLD) == []

    def test_a_robot_standing_over_several_steps_counts_once(self):
        r1 = plan_of("r1", ("wait", 5, "n1"), ("wait", 5, "n1"), ("navigate", 5, "n2"))
        assert check_node_occupancy([r1], WORLD) == []
        r2 = plan_of("r2", ("navigate", 3, "n1"))
        assert spans(check_node_occupancy([r1, r2], WORLD)) == [
            ("n1", 3, 10, [("r1", 0), ("r1", 1), ("r2", 0)])]

    def test_plans_without_end_nodes_are_not_checked(self):
        assert check_node_occupancy([plan_of("r1", ("wait", 9, None)), plan_of("r2", ("wait", 9, None))],
                                    WORLD) == []


class TestViolations:

    def test_violation_names_the_node(self):
        conflicts = check_node_occupancy([plan_of("r1", ("navigate", 5, "c")), plan_of("r2", ("navigate", 4, "c"))],
                                         WORLD)
        [v] = node_conflicts_to_violations(conflicts)
        assert (v.code, v.resource_id) == ("NODE_OCCUPIED", "c")
        assert v.refs == (StepRef(robot_id="r1", step_index=0), StepRef(robot_id="r2", step_index=0))
        assert "r1, r2" in v.message and "stop at c" in v.message

    def test_a_step_is_referred_to_once(self):
        r1 = plan_of("r1", ("wait", 5, "n1"), ("wait", 5, "n1"))
        r2 = plan_of("r2", ("navigate", 3, "n1"))
        [v] = node_conflicts_to_violations(check_node_occupancy([r1, r2], WORLD))
        assert v.refs == (StepRef(robot_id="r1", step_index=0), StepRef(robot_id="r1", step_index=1),
                          StepRef(robot_id="r2", step_index=0))


# ---------------- S-4 conflicts go to R-1 with S-1's and S-2's ----------------

class TestYielding:

    def test_s4_is_checked_with_s1(self):
        r1 = plan_of("r1", ("navigate", 5, "c"))
        r2 = plan_of("r2", ("navigate", 4, "c"))
        violations, must_fix = check_cross_plan_conflicts([r1, r2], WORLD, margin=0.0)
        assert [v.code for v in violations] == ["NODE_OCCUPIED"]
        assert must_fix == {"r1": violations}     # r2 got there first

    def test_the_robot_with_fewer_yields_gives_way(self):
        ledger = FairnessLedger()
        ledger.record_yield("r1", "earlier", 0.0, "r2")
        r1 = plan_of("r1", ("navigate", 5, "c"))
        r2 = plan_of("r2", ("navigate", 4, "c"))
        _, must_fix = check_cross_plan_conflicts([r1, r2], WORLD, ledger, margin=0.0)
        assert list(must_fix) == ["r2"]

    def test_s1_and_s4_rank_robots_by_the_same_entry_times(self):
        """b stops at n1 first and a enters the aisle first. Ranked per conflict, S-4 would send
        a back and S-1 would send b back, in one round. Ranked by each robot's first entry
        over the whole round (a at 6 s, b at 3 s), a gives way in both."""
        a = plan_of("a", ("navigate", 6, "n1"), ("wait", 4, "n1"), ("navigate", 10, None, "aisle"))
        b = plan_of("b", ("navigate", 3, "n1"), ("wait", 5, "n1"), ("navigate", 4, "x"),
                    ("navigate", 10, None, "aisle"))
        ledger = FairnessLedger()
        [s1] = check_capacity_violations([a, b], WORLD, margin=0.0)
        [s4] = check_node_occupancy([a, b], WORLD)
        assert assign_yield(s1, {"a": a, "b": b}, ledger) == ["b"]   # each conflict on its own
        assert assign_yield(s4, {"a": a, "b": b}, ledger) == ["a"]
        violations, must_fix = check_cross_plan_conflicts([a, b], WORLD, ledger, margin=0.0)
        assert [v.code for v in violations] == ["CAPACITY_EXCEEDED", "NODE_OCCUPIED"]
        assert list(must_fix) == ["a"] and must_fix["a"] == violations
