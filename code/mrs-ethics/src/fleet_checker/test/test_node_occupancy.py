"""S-4 node occupancy: two robots may not be at the same node at once, standing or driving through.

node_claims (plan.py) says when a robot is at each node; check_node_occupancy (cross_plan.py)
sweeps each node as a resource of capacity 1. Plans are written by hand with the end_node (and
some with the waypoints) the compiler would fill in.
"""
import math

import pytest

from fleet_checker.cross_plan import (
    FairnessLedger, assign_yield, check_capacity_violations, check_cross_plan_conflicts,
    check_node_occupancy, node_conflicts_to_violations,
)
from fleet_checker.plan import Plan, Region, RegionKind, StepRef, WorldMap, node_claims

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
    return [(c.resource_id, c.step_index, c.start, c.end) for c in node_claims(plan, **kwargs)]


def drive_through(robot_id, *waypoints, then=5):
    """One step reaching each (node, t) of waypoints by its end, then a drive on for `then` s."""
    end = waypoints[-1][1]
    rows = [{"action": "navigate", "region": "hall", "t_start": 0.0, "t_end": end, "end_node": waypoints[-1][0],
             "waypoints": [{"node": n, "t": t} for n, t in waypoints]},
            {"action": "navigate", "region": "hall", "t_start": end, "t_end": end + then}]
    return Plan.model_validate({"plan_id": f"{robot_id}-p", "robot_id": robot_id, "task_type": "delivery",
                                "steps": rows})


def spans(conflicts):
    return [(c.resource_id, c.t_start, c.t_end, [(x.robot_id, x.step_index) for x in c.claims])
            for c in conflicts]


# ---------------- when a robot is at a node ----------------

class TestNodeClaims:

    def test_standing_steps_and_the_node_the_plan_ends_at(self):
        # waits at n1 for 10 s, drives 5 s to n2 and stays there
        plan = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        assert stops(plan) == [("n1", 0, 0, 10), ("n2", 1, 15, math.inf)]

    def test_without_hold_final_the_robot_only_drives_to_the_plans_end(self):
        plan = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        assert stops(plan, hold_final=False, pass_time=1.0) == [("n1", 0, -0.5, 10.5), ("n2", 1, 14.5, 15.5)]

    def test_a_plan_that_ends_standing_holds_that_node(self):
        assert stops(plan_of("r1", ("navigate", 4, "n1"), ("wait", 1, "n1"))) == [("n1", 1, 4, math.inf)]

    def test_driving_through_a_node_takes_the_time_the_robot_needs_to_drive_over_it(self):
        plan = plan_of("r1", ("navigate", 5, "n1"), ("navigate", 5, "n2"), ("navigate", 5, None))
        assert stops(plan, pass_time=1.0) == [("n1", 0, 4.5, 5.5), ("n2", 1, 9.5, 10.5)]   # ends at no node
        # A robot with no length would be at a point for no time at all: nothing to claim.
        assert stops(plan) == []

    def test_every_waypoint_a_drive_reaches_counts_the_start_node_too(self):
        plan = drive_through("r1", ("n0", 0.0), ("n1", 2.0), ("n2", 5.0))
        assert stops(plan, pass_time=1.0) == [("n0", 0, -0.5, 0.5), ("n1", 0, 1.5, 2.5), ("n2", 0, 4.5, 5.5)]

    def test_margin_widens_both_ends(self):
        plan = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        assert stops(plan, margin=0.5) == [("n1", 0, -0.5, 10.5), ("n2", 1, 14.5, math.inf)]

    def test_plans_without_end_nodes_are_at_no_node(self):
        assert stops(plan_of("r1", ("wait", 10, None), ("navigate", 5, None))) == []

    def test_negative_margin_is_refused(self):
        with pytest.raises(ValueError, match="margin"):
            list(node_claims(plan_of("r1", ("wait", 1, "n1")), margin=-0.1))

    def test_a_robots_length_widens_each_stop_by_half_the_time_it_takes_to_drive_over_a_point(self):
        # pass_time = length / speed: the front reaches the node half of it before the robot's
        # middle does, and the back leaves half of it after. margin comes on top.
        plan = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        assert stops(plan, pass_time=1.0) == [("n1", 0, -0.5, 10.5), ("n2", 1, 14.5, math.inf)]
        assert stops(plan, pass_time=1.0, margin=0.5) == [("n1", 0, -1.0, 11.0),
                                                          ("n2", 1, 14.0, math.inf)]

    def test_negative_pass_time_is_refused(self):
        with pytest.raises(ValueError, match="pass_time"):
            list(node_claims(plan_of("r1", ("wait", 1, "n1")), pass_time=-0.1))


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

    def test_driving_through_where_another_robot_stands_is_a_conflict(self):
        r1 = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))
        r2 = plan_of("r2", ("navigate", 5, "n1"), ("navigate", 5, "n3"))  # drives through n1 at 5 s
        assert spans(check_node_occupancy([r1, r2], WORLD, pass_time=0.9)) == [
            ("n1", 4.55, 5.45, [("r1", 0), ("r2", 0)])]

    def test_two_robots_driving_through_one_node_at_once(self):
        r1 = drive_through("r1", ("w", 0.0), ("x", 5.0))
        r2 = drive_through("r2", ("n", 0.0), ("x", 5.5))
        [c] = check_node_occupancy([r1, r2], WORLD, pass_time=0.9)
        assert (c.resource_id, round(c.t_start, 6), round(c.t_end, 6)) == ("x", 5.05, 5.45)
        far_apart = drive_through("r2", ("n", 0.0), ("x", 7.0))
        assert check_node_occupancy([r1, far_apart], WORLD, pass_time=0.9) == []

    def test_driving_through_a_one_robot_zone_is_left_to_s1(self):
        r1 = plan_of("r1", ("navigate", 5, "a1", "aisle"), ("navigate", 5, "a2", "aisle"))
        r2 = plan_of("r2", ("navigate", 5, "a1", "aisle"), ("navigate", 5, "a3", "aisle"))
        assert check_node_occupancy([r1, r2], WORLD, pass_time=0.9) == []

    def test_a_handover_is_clean_without_margin_and_too_tight_with_it(self):
        r1 = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))   # leaves n1 at 10 s
        r2 = plan_of("r2", ("navigate", 10, "n1"))                        # arrives at n1 at 10 s
        assert check_node_occupancy([r1, r2], WORLD) == []
        assert spans(check_node_occupancy([r1, r2], WORLD, margin=0.5)) == [
            ("n1", 9.5, 10.5, [("r1", 0), ("r2", 0)])]

    def test_a_handover_is_too_tight_once_the_robots_have_a_length(self):
        # Without margin either: when r1's middle leaves n1, its back is still over it.
        r1 = plan_of("r1", ("wait", 10, "n1"), ("navigate", 5, "n2"))   # leaves n1 at 10 s
        r2 = plan_of("r2", ("navigate", 10, "n1"))                        # arrives at n1 at 10 s
        assert spans(check_node_occupancy([r1, r2], WORLD, pass_time=0.9)) == [
            ("n1", 9.55, 10.45, [("r1", 0), ("r2", 0)])]

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
        assert "r1, r2" in v.message and "be at c at once" in v.message   # standing or driving through

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


# ---------------- regression: the scenarios S-1 and S-2 are tested on ----------------

def test_in_the_scenarios_s1_and_s2_are_tested_on_only_the_crossing_has_an_s4_conflict():
    """S-4 runs on every compiled plan. Of the scenarios the S-1 and S-2 tests use, only one has
    two robots at one node at once: the crossroads, where robot_b drives through c at 4 s and
    robot_a at 5 s (1 m/s). Their middles come within 0.71 m of each other at 4.5 s, so half a
    second late, either would hit the other there."""
    from fleet_checker.adapters.sim_map import spawn_starts
    from fleet_checker.adapters.team_check import check_team_plan
    from fleet_checker.crowding import CrowdingLimit
    from fleet_checker.plan import HumanObservation
    from test_sweep_regression import SCENARIOS

    found = {}
    for name, (load, team_plans, speed, people) in SCENARIOS.items():
        sim_map = load()
        # The scenarios name every person person_1 (they are checked one at a time there).
        people = [HumanObservation(human_id=f"person_{i}", position=p.position) for i, p in enumerate(people, 1)]
        for i, team_plan in enumerate(team_plans):
            result = check_team_plan(team_plan, sim_map, spawn_starts(sim_map, ["robot_a", "robot_b"]),
                                     speed=speed, robot_length=0.45, people=people,
                                     crowding=CrowdingLimit(1.5, 1))
            for f in result.findings:
                if f.violation.code == "NODE_OCCUPIED":
                    robots = sorted({r.robot_id for r in f.violation.refs})
                    found.setdefault((name, i), []).append((f.violation.resource_id, robots))
    assert found == {("crossroads", 0): [("c", ["robot_a", "robot_b"])]}
