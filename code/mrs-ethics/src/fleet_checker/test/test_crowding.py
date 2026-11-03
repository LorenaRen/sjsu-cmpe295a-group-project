"""S-2 human crowding: when a robot is within R of a person (crowding.py), and the sweep
over each person's surroundings (cross_plan.check_crowding).

Plans here are written by hand with paths, the way the compiler fills them in; times are
worked out from the geometry, mostly with 1 m/s so that metres and seconds agree.
"""
import math
from itertools import pairwise

import pytest

from fleet_checker.cross_plan import (
    FairnessLedger, assign_yield, check_capacity_violations, check_cross_plan_conflicts,
    check_crowding, crowding_conflicts_to_violations,
)
from fleet_checker.crowding import CrowdingLimit, crowding_claims
from fleet_checker.plan import HumanObservation, Plan, Region, RegionKind, StepRef, WorldMap

ROOT3 = math.sqrt(3)

# For check_cross_plan_conflicts, which runs S-1 as well: a hall with room for everyone,
# and a one-robot aisle off it.
WORLD = WorldMap(regions={
    "hall": Region(id="hall", kind=RegionKind.OPEN_AREA, capacity=100, center=(0, 0),
                   neighbors=frozenset({"aisle"})),
    "aisle": Region(id="aisle", kind=RegionKind.AISLE, capacity=1, center=(20, 0),
                    neighbors=frozenset({"hall"})),
})


def plan_of(robot_id, *steps, t0=0.0):
    """steps: (path, seconds) or (path, seconds, region) back to back from t0; region "hall"
    unless given. A one-point path stands still."""
    rows, t = [], t0
    for path, seconds, *region in steps:
        rows.append({"action": "navigate" if len(path) > 1 else "wait",
                     "region": region[0] if region else "hall",
                     "t_start": t, "t_end": t + seconds, "path": path})
        t += seconds
    return Plan.model_validate({"plan_id": f"{robot_id}-p", "robot_id": robot_id,
                                "task_type": "delivery", "steps": rows})


def drive(robot_id, *points, t0=0.0, speed=1.0):
    """One step along the polyline `points` at `speed`."""
    length = sum(math.dist(p, q) for p, q in pairwise(points))
    return plan_of(robot_id, (points, length / speed), t0=t0)


def person(human_id, x, y):
    return HumanObservation(human_id=human_id, position=(x, y))


def stays(plan, people, radius, margin=0.0, hold_final=False):
    """(person, step, start, end) of each claim, rounded so float noise does not matter."""
    return [(c.resource_id, c.step_index, round(c.start, 6), round(c.end, 6))
            for c in crowding_claims(plan, people, radius, margin=margin, hold_final=hold_final)]


P1 = person("person_1", 0, 1)   # one metre off the x axis, which most robots below drive along


# ---------------- when is a robot within R of a person ----------------

class TestClaims:

    def test_a_straight_pass_is_worked_out_exactly(self):
        # (x - 0)^2 + (0 - 1)^2 <= 2^2  ->  |x| <= sqrt(3); x = t - 5 at 1 m/s
        plan = drive("r1", (-5, 0), (5, 0))
        [c] = crowding_claims(plan, [P1], radius=2.0, hold_final=False)
        assert (c.resource_id, c.robot_id, c.step_index) == ("person_1", "r1", 0)
        assert (c.start, c.end) == pytest.approx((5 - ROOT3, 5 + ROOT3))

    def test_a_robot_that_stays_far_away_has_no_claim(self):
        assert stays(drive("r1", (-5, 0), (5, 0)), [person("p", 0, 3)], radius=2.0) == []

    def test_touching_the_edge_for_an_instant_is_not_a_stay(self):
        assert stays(drive("r1", (-5, 0), (5, 0)), [person("p", 0, 2)], radius=2.0) == []

    def test_standing_still_inside_claims_the_whole_step(self):
        plan = plan_of("r1", (((0, 0),), 10))
        assert stays(plan, [P1], radius=2.0) == [("person_1", 0, 0, 10)]

    def test_standing_still_outside_has_no_claim(self):
        assert stays(plan_of("r1", (((0, 4),), 10)), [P1], radius=2.0) == []

    def test_ending_inside_is_held_to_infinity_passing_through_is_not(self):
        stop_there = drive("r1", (-5, 0), (0, 0))
        assert stays(stop_there, [P1], radius=2.0, hold_final=True) == [
            ("person_1", 0, round(5 - ROOT3, 6), math.inf)]
        pass_by = drive("r1", (-5, 0), (5, 0))
        assert stays(pass_by, [P1], radius=2.0, hold_final=True) == [
            ("person_1", 0, round(5 - ROOT3, 6), round(5 + ROOT3, 6))]

    def test_a_parked_robot_inside_is_held_to_infinity(self):
        assert stays(plan_of("r1", (((0, 0),), 1)), [P1], radius=2.0, hold_final=True) == [
            ("person_1", 0, 0, math.inf)]

    def test_margin_widens_both_ends(self):
        plan = drive("r1", (-5, 0), (5, 0))
        assert stays(plan, [P1], radius=2.0, margin=0.5) == [
            ("person_1", 0, round(4.5 - ROOT3, 6), round(5.5 + ROOT3, 6))]

    def test_turning_inside_the_disc_is_one_stay(self):
        # in from x = -1 (t = 2) to the corner (t = 3), out at y = 1 (t = 4)
        plan = drive("r1", (-3, 0), (0, 0), (0, 3))
        assert stays(plan, [person("p", 0, 0)], radius=1.0) == [("p", 0, 2, 4)]

    def test_leaving_and_coming_back_within_one_step_gives_two_claims(self):
        plan = drive("r1", (-2, 0), (2, 0), (-2, 0))
        assert stays(plan, [person("p", 0, 0)], radius=1.0) == [("p", 0, 1, 3), ("p", 0, 5, 7)]

    def test_each_step_gets_its_own_claim(self):
        plan = plan_of("r1", (((-5, 0), (0, 0)), 5), (((0, 0), (5, 0)), 5))
        assert stays(plan, [P1], radius=2.0) == [
            ("person_1", 0, round(5 - ROOT3, 6), 5), ("person_1", 1, 5, round(5 + ROOT3, 6))]

    def test_speed_is_constant_along_the_whole_step(self):
        # 4 m in 8 s; the second piece starts at t = 6 and is within 0.5 m of (3, 1) from y = 0.5
        plan = plan_of("r1", (((0, 0), (3, 0), (3, 1)), 8))
        assert stays(plan, [person("p", 3, 1)], radius=0.5) == [("p", 0, 7, 8)]

    def test_claims_for_each_person(self):
        plan = drive("r1", (-5, 0), (5, 0))
        people = [person("left", -4, 0), person("right", 4, 0)]
        assert stays(plan, people, radius=1.0) == [("left", 0, 0, 2), ("right", 0, 8, 10)]

    def test_a_plan_without_paths_is_refused(self):
        plan = Plan.model_validate({"plan_id": "p", "robot_id": "r1", "task_type": "delivery",
                                    "steps": [{"action": "wait", "region": "hall",
                                               "t_start": 0, "t_end": 1}]})
        with pytest.raises(ValueError, match="no paths"):
            list(crowding_claims(plan, [P1], radius=2.0))

    def test_negative_margin_is_refused(self):
        with pytest.raises(ValueError, match="margin"):
            list(crowding_claims(drive("r1", (0, 0), (1, 0)), [P1], radius=2.0, margin=-0.1))


class TestLimit:

    @pytest.mark.parametrize("radius", [0.0, -1.0, math.inf, math.nan])
    def test_radius_must_be_a_positive_number_of_metres(self, radius):
        with pytest.raises(ValueError, match="radius"):
            CrowdingLimit(radius=radius, max_robots=1)

    @pytest.mark.parametrize("max_robots", [0, -1, 1.5])
    def test_max_robots_must_be_a_whole_number_of_at_least_one(self, max_robots):
        with pytest.raises(ValueError, match="max_robots"):
            CrowdingLimit(radius=1.5, max_robots=max_robots)


# ---------------- more than N robots near one person at once ----------------

ONE = CrowdingLimit(radius=2.0, max_robots=1)
ORIGIN = person("person_1", 0, 0)


def along(robot_id, y, t0=0.0):
    """Drive from x = -5 to x = 5 at height y, 1 m/s: within 2 m of the origin while |x| <= sqrt(4 - y^2)."""
    return drive(robot_id, (-5, y), (5, y), t0=t0)


class TestCheckCrowding:

    def test_two_robots_near_one_person_at_once(self):
        [c] = check_crowding([along("r1", 1), along("r2", -1)], [ORIGIN], ONE, hold_final=False)
        assert (c.resource_id, c.capacity, c.is_edge_swap) == ("person_1", 1, False)
        assert [x.robot_id for x in c.claims] == ["r1", "r2"]
        assert (c.t_start, c.t_end) == pytest.approx((5 - ROOT3, 5 + ROOT3))

    def test_robots_passing_one_after_the_other_do_not_crowd(self):
        plans = [along("r1", 1), along("r2", -1, t0=10)]
        assert check_crowding(plans, [ORIGIN], ONE, hold_final=False) == []

    def test_two_robots_are_allowed_when_n_is_two_but_three_are_not(self):
        two = CrowdingLimit(radius=2.0, max_robots=2)
        assert check_crowding([along("r1", 1), along("r2", -1)], [ORIGIN], two, hold_final=False) == []
        [c] = check_crowding([along("r1", 1), along("r2", -1), along("r3", 0.5)], [ORIGIN], two,
                             hold_final=False)
        assert sorted({x.robot_id for x in c.claims}) == ["r1", "r2", "r3"]

    def test_a_robot_over_several_steps_counts_once(self):
        # Two steps, both near the person; with a margin their claims overlap.
        r1 = plan_of("r1", (((-1, 0), (0, 0)), 1), (((0, 0), (1, 0)), 1))
        assert check_crowding([r1], [ORIGIN], ONE, margin=0.5, hold_final=False) == []
        r2 = drive("r2", (-1, 1), (1, 1))   # near the person from t = 0 to t = 2
        [c] = check_crowding([r1, r2], [ORIGIN], ONE, margin=0.5, hold_final=False)
        assert [(x.robot_id, x.step_index) for x in c.claims] == [("r1", 0), ("r1", 1), ("r2", 0)]

    def test_each_person_is_counted_separately(self):
        far = person("person_2", 100, 0)
        apart = [along("r1", 1), drive("r2", (95, 1), (105, 1))]
        assert check_crowding(apart, [ORIGIN, far], ONE, hold_final=False) == []
        together = [along("r1", 1), along("r2", -1)]
        assert [c.resource_id for c in check_crowding(together, [ORIGIN, far], ONE,
                                                      hold_final=False)] == ["person_1"]

    def test_a_robot_near_the_person_across_two_steps_is_one_conflict_without_margin(self):
        """r1 is near the person from 5 - sqrt(3) s to 5 + sqrt(3) s, over two steps that meet at 5 s."""
        r1 = plan_of("r1", (((-5, 1), (0, 1)), 5), (((0, 1), (5, 1)), 5))
        [c] = check_crowding([r1, along("r2", -1)], [ORIGIN], ONE, margin=0.0, hold_final=False)
        assert (c.t_start, c.t_end) == pytest.approx((5 - ROOT3, 5 + ROOT3))
        assert [(x.robot_id, x.step_index) for x in c.claims] == [("r1", 0), ("r1", 1), ("r2", 0)]

    def test_a_robot_parked_near_a_person_takes_the_place_for_good(self):
        parked = plan_of("r1", (((0, 1),), 1))
        later = along("r2", -1, t0=100)
        [c] = check_crowding([parked, later], [ORIGIN], ONE)
        assert (c.t_start, c.t_end) == pytest.approx((105 - ROOT3, 105 + ROOT3))

    def test_no_people_no_conflicts(self):
        assert check_crowding([along("r1", 1), along("r2", -1)], [], ONE) == []

    def test_two_people_with_one_id_are_refused(self):
        with pytest.raises(ValueError, match="person_1"):
            check_crowding([along("r1", 1)], [ORIGIN, person("person_1", 9, 9)], ONE)


class TestViolations:

    def test_violation_names_the_person_the_radius_and_the_limit(self):
        limit = CrowdingLimit(radius=1.5, max_robots=1)
        conflicts = check_crowding([along("r1", 1), along("r2", -1)], [ORIGIN], limit, hold_final=False)
        [v] = crowding_conflicts_to_violations(conflicts, limit)
        assert (v.code, v.resource_id) == ("HUMAN_CROWDING", "person_1")
        assert v.refs == (StepRef(robot_id="r1", step_index=0), StepRef(robot_id="r2", step_index=0))
        assert "r1, r2" in v.message and "within 1.5 m of person_1" in v.message
        assert "at most 1 robot" in v.message

    def test_a_step_is_referred_to_once(self):
        # r1 passes the person twice in one step (t = 1-3 and 5-7), while r2 and r3 stand
        # next to them the whole time: one conflict, with two claims of r1's step 0.
        r1 = drive("r1", (-2, 0), (2, 0), (-2, 0))
        r2 = plan_of("r2", (((0, -0.5),), 10))
        r3 = plan_of("r3", (((0, 0.5),), 10))
        limit = CrowdingLimit(radius=1.0, max_robots=1)
        [c] = check_crowding([r1, r2, r3], [ORIGIN], limit, hold_final=False)
        assert [x.robot_id for x in c.claims] == ["r1", "r1", "r2", "r3"]
        [v] = crowding_conflicts_to_violations([c], limit)
        assert v.refs == (StepRef(robot_id="r1", step_index=0), StepRef(robot_id="r2", step_index=0),
                          StepRef(robot_id="r3", step_index=0))


# ---------------- S-2 conflicts go to R-1 and F-2, as S-1's do ----------------

def both_near(*robots):
    """Every robot drives past the person at once (heights 1, -1, 0.5, ...)."""
    heights = (1, -1, 0.5)
    return [along(r, y) for r, y in zip(robots, heights)]


class TestYielding:

    def test_s2_is_checked_with_s1_when_people_are_given(self):
        violations, must_fix = check_cross_plan_conflicts(both_near("r1", "r2"), WORLD, margin=0.0,
                                                          people=[ORIGIN], crowding=ONE)
        assert [v.code for v in violations] == ["HUMAN_CROWDING"]
        assert must_fix == {"r1": violations}   # a full tie: robot_id decides

    def test_without_people_s2_is_not_checked(self):
        assert check_cross_plan_conflicts(both_near("r1", "r2"), WORLD) == ([], {})

    def test_people_without_a_limit_are_refused(self):
        with pytest.raises(ValueError, match="CrowdingLimit"):
            check_cross_plan_conflicts(both_near("r1"), WORLD, people=[ORIGIN])

    def test_the_robot_with_fewer_yields_gives_way(self):
        ledger = FairnessLedger()
        ledger.record_yield("r1", "earlier", 0.0, "r2")
        _, must_fix = check_cross_plan_conflicts(both_near("r1", "r2"), WORLD, ledger, margin=0.0,
                                                 people=[ORIGIN], crowding=ONE)
        assert list(must_fix) == ["r2"]

    def test_a_starving_robot_keeps_its_place(self):
        ledger = FairnessLedger()
        ledger.record_yield("r2", "earlier", 0.0, "r1")   # by R-1 alone, r1 would give way
        for _ in range(ledger.starvation_threshold):
            ledger.record_rejection("r1")
        _, must_fix = check_cross_plan_conflicts(both_near("r1", "r2"), WORLD, ledger, margin=0.0,
                                                 people=[ORIGIN], crowding=ONE)
        assert list(must_fix) == ["r2"]

    def test_n_robots_near_a_person_with_room_for_one_send_n_minus_one_back(self):
        # r3 drives closest to the x axis, so it is the first within 2 m of the person.
        _, must_fix = check_cross_plan_conflicts(both_near("r1", "r2", "r3"), WORLD, margin=0.0,
                                                 people=[ORIGIN], crowding=ONE)
        assert sorted(must_fix) == ["r1", "r2"]

    def test_s1_and_s2_rank_robots_by_the_same_entry_times(self):
        """b reaches the person first and a reaches the aisle first. Ranked per conflict, S-2
        would send a back and S-1 would send b back: "a waits for b" and "b waits for a" in
        one round. Ranked by each robot's first entry over the whole round, a gives way in both."""
        a = plan_of("a", (((3, 0),), 4), (((3, 0), (0.5, 0)), 2.5), (((0.5, 0),), 3.5),
                    (((0.5, 0), (10.5, 0)), 10, "aisle"))     # near the person from 6 s, aisle from 10 s
        b = plan_of("b", (((-3, 0),), 0.5), (((-3, 0), (-0.5, 0)), 2.5), (((-0.5, 0),), 9),
                    (((-0.5, 0), (-10.5, 0)), 10, "aisle"))   # near the person from 2.5 s, aisle from 12 s
        near = CrowdingLimit(radius=1.0, max_robots=1)
        ledger = FairnessLedger()
        [s1] = check_capacity_violations([a, b], WORLD, margin=0.5)
        [s2] = check_crowding([a, b], [ORIGIN], near, margin=0.5)
        assert assign_yield(s1, {"a": a, "b": b}, ledger) == ["b"]   # each conflict on its own
        assert assign_yield(s2, {"a": a, "b": b}, ledger) == ["a"]
        violations, must_fix = check_cross_plan_conflicts([a, b], WORLD, ledger, margin=0.5,
                                                          people=[ORIGIN], crowding=near)
        assert [v.code for v in violations] == ["CAPACITY_EXCEEDED", "HUMAN_CROWDING"]
        assert list(must_fix) == ["a"] and must_fix["a"] == violations
