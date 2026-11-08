"""Tests for cross-plan conflict detection (S-1) and yield assignment (R-1)."""
import math

import pytest

from fleet_checker.plan import (
    Claim, Plan, PlanStep, Region, RegionKind, Resource, TaskType, WorldMap,
)
from fleet_checker.cross_plan import (
    CapacityConflict, FairnessLedger, OverCapacity, assign_yield,
    capacity_conflicts_to_violations, check_capacity_violations,
    check_cross_plan_conflicts, sweep_over_capacity,
)


# Same test world as test_plan.py
WORLD = WorldMap(
    regions={
        "room_a":    Region(id="room_a", kind=RegionKind.OPEN_AREA, capacity=3, center=(0, 0),
                            neighbors=frozenset({"door_1", "charger_1"})),
        "charger_1": Region(id="charger_1", kind=RegionKind.CHARGER, capacity=1, center=(0, 3),
                            neighbors=frozenset({"room_a"})),
        "door_1":    Region(id="door_1", kind=RegionKind.DOORWAY, capacity=1, center=(5, 0),
                            neighbors=frozenset({"room_a", "aisle_1"})),
        "aisle_1":   Region(id="aisle_1", kind=RegionKind.AISLE, capacity=1, center=(10, 0),
                            neighbors=frozenset({"door_1", "door_2"})),
        "door_2":    Region(id="door_2", kind=RegionKind.DOORWAY, capacity=1, center=(15, 0),
                            neighbors=frozenset({"aisle_1", "room_b"})),
        "room_b":    Region(id="room_b", kind=RegionKind.OPEN_AREA, capacity=3, center=(20, 0),
                            neighbors=frozenset({"door_2"})),
    },
    resources={"elevator_call": Resource(id="elevator_call", capacity=1)},
)


def make_plan(robot_id: str, path: list, step_s: float = 10.0, t0: float = 0.0,
              task_type: str = "delivery", **overrides) -> Plan:
    """path: list of (action, region) pairs; each step lasts step_s seconds."""
    steps = [
        {"action": a, "region": r, "t_start": t0 + i * step_s, "t_end": t0 + (i + 1) * step_s}
        for i, (a, r) in enumerate(path)
    ]
    data = {"plan_id": f"{robot_id}-p1", "robot_id": robot_id,
            "task_type": task_type, "steps": steps} | overrides
    return Plan.model_validate(data)


def codes(violations):
    return [v.code for v in violations]


# ---------------- S-1: Capacity Violation Tests ----------------

class TestCapacityViolation:
    """Tests for S-1 interval capacity violation detection."""

    def test_no_conflict_when_plans_dont_overlap(self):
        """Two robots in same region at different times - no conflict."""
        r1 = make_plan("r1", [("wait", "aisle_1")], t0=0.0)
        r2 = make_plan("r2", [("wait", "aisle_1")], t0=20.0)

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert len(conflicts) == 0

    def test_capacity_exceeded_same_region(self):
        """Two robots in capacity-1 region at same time - conflict."""
        r1 = make_plan("r1", [("wait", "aisle_1")], t0=0.0)
        r2 = make_plan("r2", [("wait", "aisle_1")], t0=5.0)

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert len(conflicts) == 1
        assert conflicts[0].resource_id == "aisle_1"
        assert not conflicts[0].is_edge_swap

    def test_no_conflict_in_high_capacity_region(self):
        """Two robots in capacity-3 region - no conflict."""
        r1 = make_plan("r1", [("wait", "room_a")], t0=0.0)
        r2 = make_plan("r2", [("wait", "room_a")], t0=5.0)

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert len(conflicts) == 0

    def test_capacity_exceeded_four_robots_in_cap3(self):
        """Four robots in capacity-3 region - conflict."""
        plans = [make_plan(f"r{i}", [("wait", "room_a")], t0=0.0) for i in range(4)]

        conflicts = check_capacity_violations(plans, WORLD, margin=0.0, hold_final=False)
        assert len(conflicts) == 1
        assert conflicts[0].resource_id == "room_a"

    def test_edge_swap_detected_with_margin(self):
        """Two robots swapping adjacent regions head-on - detected as EDGE_SWAP."""
        # r1: aisle_1 -> door_2
        # r2: door_2 -> aisle_1
        r1 = make_plan("r1", [("navigate", "aisle_1"), ("navigate", "door_2")])
        r2 = make_plan("r2", [("navigate", "door_2"), ("navigate", "aisle_1")])

        # Without margin, half-open intervals don't overlap
        conflicts_no_margin = check_capacity_violations([r1, r2], WORLD, margin=0.0)
        # Filter out conflicts from hold_final
        initial_conflicts = [c for c in conflicts_no_margin if c.t_start < 20]
        assert len(initial_conflicts) == 0

        # With margin, the swap becomes visible
        conflicts_with_margin = check_capacity_violations([r1, r2], WORLD, margin=0.5, hold_final=False)
        assert len(conflicts_with_margin) >= 1
        swap_conflicts = [c for c in conflicts_with_margin if c.is_edge_swap]
        assert len(swap_conflicts) >= 1

    def test_robot_does_not_conflict_with_itself(self):
        """Consecutive steps in the same region overlap by 2*margin; that is one robot, not two."""
        r1 = make_plan("r1", [("wait", "aisle_1"), ("wait", "aisle_1")])

        conflicts = check_capacity_violations([r1], WORLD, margin=0.5, hold_final=False)
        assert conflicts == []

    def test_consecutive_steps_in_one_region_are_one_conflict_without_margin(self):
        """r1 waits in aisle_1 for two steps in a row; r2 is there from 5 s to 15 s."""
        r1 = make_plan("r1", [("wait", "aisle_1"), ("wait", "aisle_1")])
        r2 = make_plan("r2", [("wait", "aisle_1")], t0=5.0)
        [c] = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert (c.t_start, c.t_end) == (5.0, 15.0)
        assert [(x.robot_id, x.step_index) for x in c.claims] == [("r1", 0), ("r1", 1), ("r2", 0)]

    def test_capacity_counts_robots_not_claims(self):
        """Three robots in capacity-3 room_a is fine even if one robot holds two overlapping claims."""
        r1 = make_plan("r1", [("wait", "room_a"), ("wait", "room_a")])
        r2 = make_plan("r2", [("wait", "room_a"), ("wait", "room_a")])
        r3 = make_plan("r3", [("wait", "room_a"), ("wait", "room_a")])

        conflicts = check_capacity_violations([r1, r2, r3], WORLD, margin=0.5, hold_final=False)
        assert conflicts == []

    def test_parked_robot_blocks_later_traffic(self):
        """Robot holding final position blocks another robot."""
        r1 = make_plan("r1", [("navigate", "door_1"), ("wait", "aisle_1")])
        r2 = make_plan("r2", [("navigate", "door_2"), ("navigate", "aisle_1")], t0=20.0)

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=True)
        aisle_conflicts = [c for c in conflicts if c.resource_id == "aisle_1"]
        assert len(aisle_conflicts) == 1


def claim(robot_id: str, start: float, end: float, step: int = 0) -> Claim:
    return Claim("aisle_1", robot_id, step, start, end)


class TestSweepOverCapacity:
    """The sweep S-1 and S-2 share: the claims on one resource -> stretches of time over capacity."""

    def test_overlap_beyond_capacity_is_one_stretch(self):
        out = sweep_over_capacity([claim("r1", 0, 10), claim("r2", 5, 15)], capacity=1)
        assert out == [OverCapacity((claim("r1", 0, 10), claim("r2", 5, 15)), 5, 10)]

    def test_end_and_start_at_the_same_moment_do_not_overlap(self):
        """Half-open intervals: r1 leaving at t=10 and r2 arriving at t=10 is a clean handover."""
        assert sweep_over_capacity([claim("r1", 0, 10), claim("r2", 10, 20)], capacity=1) == []

    def test_counts_robots_not_claims(self):
        """r1's two claims overlap, as consecutive steps do with a margin: still one robot."""
        claims = [claim("r1", 0, 10, step=0), claim("r1", 9, 20, step=1), claim("r2", 5, 15)]
        assert sweep_over_capacity(claims, capacity=2) == []
        [stretch] = sweep_over_capacity(claims, capacity=1)
        assert (stretch.t_start, stretch.t_end) == (5, 15)
        assert stretch.claims == (claim("r1", 0, 10, step=0), claim("r1", 9, 20, step=1),
                                  claim("r2", 5, 15))

    def test_chained_overlaps_are_one_stretch(self):
        """r1 meets r2 and r2 meets r3: one stretch with all three (assign_yield's over-yield note)."""
        claims = [claim("r1", 0, 10), claim("r2", 5, 15), claim("r3", 8, 20)]
        [stretch] = sweep_over_capacity(claims, capacity=1)
        assert (stretch.t_start, stretch.t_end) == (5, 15)
        assert [c.robot_id for c in stretch.claims] == ["r1", "r2", "r3"]

    def test_separate_stretches_come_in_time_order(self):
        claims = [claim("r1", 0, 10), claim("r2", 5, 11), claim("r3", 12, 20), claim("r4", 15, 25)]
        out = sweep_over_capacity(claims, capacity=1)
        assert [(s.t_start, s.t_end) for s in out] == [(5, 10), (15, 20)]
        assert [[c.robot_id for c in s.claims] for s in out] == [["r1", "r2"], ["r3", "r4"]]

    def test_claims_held_to_infinity(self):
        out = sweep_over_capacity([claim("r1", 0, math.inf), claim("r2", 5, math.inf)], capacity=1)
        assert [(s.t_start, s.t_end) for s in out] == [(5, math.inf)]

    def test_capacity_two_needs_a_third_robot(self):
        claims = [claim("r1", 0, 10), claim("r2", 2, 10), claim("r3", 4, 6)]
        out = sweep_over_capacity(claims, capacity=2)
        assert [(s.t_start, s.t_end) for s in out] == [(4, 6)]
        assert [c.robot_id for c in out[0].claims] == ["r1", "r2", "r3"]

    def test_a_robot_handing_over_to_its_next_claim_stays_present(self):
        """With margin = 0, r1's consecutive steps touch at t = 10. It is there throughout:
        one stretch, not one that ends at 10 and another that starts at 10."""
        claims = [claim("r1", 0, 10, step=0), claim("r1", 10, 20, step=1), claim("r2", 5, 15)]
        [stretch] = sweep_over_capacity(claims, capacity=1)
        assert (stretch.t_start, stretch.t_end) == (5, 15)
        assert stretch.claims == tuple(claims)

    def test_a_gap_of_up_to_1e_6_s_is_a_handover_too(self):
        claims = [claim("r1", 0, 10, step=0), claim("r1", 10 + 5e-7, 20, step=1), claim("r2", 5, 15)]
        assert [(s.t_start, s.t_end) for s in sweep_over_capacity(claims, capacity=1)] == [(5, 15)]

    def test_a_longer_gap_means_the_robot_was_away(self):
        claims = [claim("r1", 0, 10, step=0), claim("r1", 10.5, 20, step=1), claim("r2", 5, 15)]
        out = sweep_over_capacity(claims, capacity=1)
        assert [(s.t_start, s.t_end) for s in out] == [(5, 10), (10.5, 15)]


class TestCapacityConflictToViolation:
    """Tests for converting conflicts to violations."""

    def test_capacity_exceeded_violation_format(self):
        """CAPACITY_EXCEEDED violation has correct format."""
        r1 = make_plan("r1", [("wait", "aisle_1")], t0=0.0)
        r2 = make_plan("r2", [("wait", "aisle_1")], t0=5.0)

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        violations = capacity_conflicts_to_violations(conflicts)

        assert len(violations) == 1
        assert violations[0].code == "CAPACITY_EXCEEDED"
        assert "aisle_1" in violations[0].message
        assert len(violations[0].refs) == 2

    def test_violation_names_the_region_it_is_about(self):
        r1 = make_plan("r1", [("wait", "aisle_1")], t0=0.0)
        r2 = make_plan("r2", [("wait", "aisle_1")], t0=5.0)
        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert [v.resource_id for v in capacity_conflicts_to_violations(conflicts)] == ["aisle_1"]

    def test_edge_swap_violation_format(self):
        """EDGE_SWAP violation has correct format."""
        r1 = make_plan("r1", [("navigate", "aisle_1"), ("navigate", "door_2")])
        r2 = make_plan("r2", [("navigate", "door_2"), ("navigate", "aisle_1")])

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.5, hold_final=False)
        swap_conflicts = [c for c in conflicts if c.is_edge_swap]
        violations = capacity_conflicts_to_violations(swap_conflicts)

        assert len(violations) >= 1
        assert violations[0].code == "EDGE_SWAP"
        assert "swap" in violations[0].message.lower()


# ---------------- R-1: Yield Assignment Tests ----------------

class TestYieldAssignment:
    """Tests for R-1 yield assignment."""

    def test_robot_with_fewer_yields_yields(self):
        """Robot with fewer cumulative yields should yield (R-1 rule)."""
        r1 = make_plan("r1", [("wait", "aisle_1")])
        r2 = make_plan("r2", [("wait", "aisle_1")])
        plans = {"r1": r1, "r2": r2}

        ledger = FairnessLedger()
        # r1 has yielded twice already, r2 has yielded zero times
        ledger.record_yield("r1", "c1", 0.0, "r2")
        ledger.record_yield("r1", "c2", 10.0, "r2")

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert len(conflicts) == 1

        yielders = assign_yield(conflicts[0], plans, ledger)
        # Per R-1: "The robot with fewer cumulative yields yields this time"
        # r2 has 0 yields (fewer than r1's 2), so r2 should yield
        assert yielders == ["r2"]

    def test_robot_with_more_yields_is_protected(self):
        """Robot that has yielded more should NOT yield again (fairness)."""
        r1 = make_plan("r1", [("wait", "aisle_1")])
        r2 = make_plan("r2", [("wait", "aisle_1")])
        plans = {"r1": r1, "r2": r2}

        ledger = FairnessLedger()
        # Both have yielded, but r1 has yielded more
        ledger.record_yield("r1", "c1", 0.0, "r2")
        ledger.record_yield("r1", "c2", 10.0, "r2")
        ledger.record_yield("r2", "c3", 20.0, "r1")

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        yielders = assign_yield(conflicts[0], plans, ledger)
        # r2 has 1 yield, r1 has 2 yields. r2 (fewer) should yield
        assert yielders == ["r2"]

    def test_task_type_tiebreak(self):
        """SERVICE yields to DELIVERY when yield counts are equal."""
        r1 = make_plan("r1", [("wait", "aisle_1")], task_type="delivery")
        r2 = make_plan("r2", [("wait", "aisle_1")], task_type="service")
        plans = {"r1": r1, "r2": r2}

        ledger = FairnessLedger()  # Both have 0 yields

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        yielders = assign_yield(conflicts[0], plans, ledger)

        # SERVICE has lower priority, so r2 (service) yields
        assert yielders == ["r2"]

    def test_later_entry_yields_when_r1_ties(self):
        """Equal yields and task types: the robot that enters the region later yields."""
        r1 = make_plan("r1", [("wait", "aisle_1")], step_s=30.0)               # in aisle_1 from t=0
        r2 = make_plan("r2", [("navigate", "door_1"), ("wait", "aisle_1")])     # enters at t=10
        plans = {"r1": r1, "r2": r2}

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert len(conflicts) == 1

        # robot_id order alone would pick r1
        assert assign_yield(conflicts[0], plans, FairnessLedger()) == ["r2"]

    def test_task_type_decides_before_entry_time(self):
        """r1 is in the region first, but SERVICE still yields to DELIVERY."""
        r1 = make_plan("r1", [("wait", "aisle_1")], step_s=30.0, task_type="service")
        r2 = make_plan("r2", [("navigate", "door_1"), ("wait", "aisle_1")], task_type="delivery")
        plans = {"r1": r1, "r2": r2}

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert assign_yield(conflicts[0], plans, FairnessLedger()) == ["r1"]

    def test_entry_times_that_differ_only_by_rounding_are_a_tie(self):
        """0.1 + 0.2 != 0.3 in floating point; that must not decide who yields."""
        r1 = make_plan("r1", [("wait", "aisle_1")], t0=0.3)
        r2 = make_plan("r2", [("wait", "aisle_1")], t0=0.1 + 0.2)
        plans = {"r1": r1, "r2": r2}

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert assign_yield(conflicts[0], plans, FairnessLedger()) == ["r1"]   # robot_id

    def test_starvation_override(self):
        """Starving robot (F-2) gets priority, others yield."""
        r1 = make_plan("r1", [("wait", "aisle_1")], task_type="service")
        r2 = make_plan("r2", [("wait", "aisle_1")], task_type="delivery")
        plans = {"r1": r1, "r2": r2}

        ledger = FairnessLedger()
        # r1 has been rejected 3 times consecutively (starving)
        for _ in range(3):
            ledger.record_rejection("r1")

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        yielders = assign_yield(conflicts[0], plans, ledger)

        # r1 is starving, so r2 must yield even though it's DELIVERY
        assert yielders == ["r2"]

    def test_starvation_threshold_comes_from_the_ledger(self):
        """With M = 1, a single rejection is enough for F-2."""
        r1 = make_plan("r1", [("wait", "aisle_1")], task_type="service")
        r2 = make_plan("r2", [("wait", "aisle_1")], task_type="delivery")
        plans = {"r1": r1, "r2": r2}

        ledger = FairnessLedger(starvation_threshold=1)
        ledger.record_rejection("r1")

        conflicts = check_capacity_violations([r1, r2], WORLD, margin=0.0, hold_final=False)
        assert assign_yield(conflicts[0], plans, ledger) == ["r2"]

    def test_starvation_picks_yielder_in_same_direction_as_r1(self):
        """Among non-starving robots, the one with FEWER yields yields, as in R-1."""
        # 4 robots in capacity-3 room_a: exactly one must yield
        plans = {f"r{i}": make_plan(f"r{i}", [("wait", "room_a")]) for i in range(4)}

        ledger = FairnessLedger()
        for _ in range(3):
            ledger.record_rejection("r0")                       # r0 starving
        ledger.record_yield("r2", "c1", 10.0, "r1")
        ledger.record_yield("r2", "c2", 11.0, "r1")             # r2: 2 yields
        ledger.record_yield("r3", "c3", 12.0, "r1")             # r3: 1 yield, r1: 0

        conflicts = check_capacity_violations(list(plans.values()), WORLD,
                                              margin=0.0, hold_final=False)
        yielders = assign_yield(conflicts[0], plans, ledger)

        assert yielders == ["r1"]

    def test_three_robots_in_capacity_one_two_yield(self):
        """n robots in a capacity-c region: n - c yield, chosen in R-1 order."""
        plans = {f"r{i}": make_plan(f"r{i}", [("wait", "aisle_1")]) for i in range(3)}

        ledger = FairnessLedger()
        ledger.record_yield("r0", "c1", 0.0, "r1")
        ledger.record_yield("r0", "c2", 1.0, "r1")              # r0: 2 yields
        ledger.record_yield("r2", "c3", 2.0, "r1")              # r2: 1, r1: 0

        conflicts = check_capacity_violations(list(plans.values()), WORLD,
                                              margin=0.0, hold_final=False)
        assert len(conflicts) == 1
        yielders = assign_yield(conflicts[0], plans, ledger)

        # r0 has yielded most, so it keeps its plan
        assert yielders == ["r1", "r2"]

    def test_five_robots_in_capacity_three_two_yield(self):
        plans = {f"r{i}": make_plan(f"r{i}", [("wait", "room_a")]) for i in range(5)}

        conflicts = check_capacity_violations(list(plans.values()), WORLD,
                                              margin=0.0, hold_final=False)
        yielders = assign_yield(conflicts[0], plans, FairnessLedger())

        assert len(yielders) == 2

    def test_multiple_starving_robots_ranked_by_r1_when_slots_run_out(self):
        """More starving robots than capacity: starving robots yield in R-1 order."""
        plans = {f"r{i}": make_plan(f"r{i}", [("wait", "aisle_1")]) for i in range(3)}

        ledger = FairnessLedger()
        for i in range(3):
            ledger.record_yield("r0", f"a{i}", float(i), "r2")  # r0: 3 yields
            ledger.record_rejection("r0")                       #     and starving
        for i in range(4):
            ledger.record_yield("r1", f"b{i}", float(i), "r2")  # r1: 4 yields
            ledger.record_rejection("r1")                       #     and starving
        # r2 not starving

        conflicts = check_capacity_violations(list(plans.values()), WORLD,
                                              margin=0.0, hold_final=False)
        yielders = assign_yield(conflicts[0], plans, ledger)

        # r2 yields first (not starving); one starving robot must also yield,
        # and R-1 picks the one with fewer yields (r0). r1 keeps its plan.
        assert yielders == ["r2", "r0"]


class TestFairnessLedger:
    """Tests for FairnessLedger tracking."""

    def test_yield_count(self):
        """Yield count tracks correctly."""
        ledger = FairnessLedger()
        assert ledger.yield_count("r1") == 0

        ledger.record_yield("r1", "c1", 0.0, "r2")
        assert ledger.yield_count("r1") == 1

        ledger.record_yield("r1", "c2", 10.0, "r2")
        assert ledger.yield_count("r1") == 2
        assert ledger.yield_count("r2") == 0

    def test_starvation_detection(self):
        """Starvation is detected after threshold rejections."""
        ledger = FairnessLedger()
        assert not ledger.is_starving("r1")

        ledger.record_rejection("r1")
        ledger.record_rejection("r1")
        assert not ledger.is_starving("r1")

        ledger.record_rejection("r1")
        assert ledger.is_starving("r1")

    def test_yields_do_not_count_as_rejections(self):
        """A yield is recorded when a conflict is resolved; it does not feed F-2."""
        ledger = FairnessLedger()
        for i in range(3):
            ledger.record_yield("r1", f"c{i}", float(i), "r2")

        assert ledger.yield_count("r1") == 3
        assert ledger.consecutive_rejections["r1"] == 0
        assert not ledger.is_starving("r1")

    def test_rejections_do_not_count_as_yields(self):
        ledger = FairnessLedger()
        for _ in range(3):
            ledger.record_rejection("r1")
        assert ledger.yield_count("r1") == 0

    def test_acceptance_resets_consecutive_rejections(self):
        """Plan acceptance resets consecutive rejection count."""
        ledger = FairnessLedger()
        ledger.record_rejection("r1")
        ledger.record_rejection("r1")

        ledger.record_acceptance("r1")
        assert ledger.consecutive_rejections["r1"] == 0
        assert not ledger.is_starving("r1")

    def test_starvation_threshold_must_be_at_least_one(self):
        with pytest.raises(ValueError, match="starvation_threshold"):
            FairnessLedger(starvation_threshold=0)


# ---------------- Integration Tests ----------------

class TestCrossPlanConflicts:
    """Integration tests for the full check_cross_plan_conflicts function."""

    def test_returns_violations_and_assignments(self):
        """Full function returns both violations and robot assignments."""
        r1 = make_plan("r1", [("wait", "aisle_1")], task_type="delivery")
        r2 = make_plan("r2", [("wait", "aisle_1")], task_type="service")

        all_violations, robot_violations = check_cross_plan_conflicts(
            [r1, r2], WORLD, margin=0.0
        )

        assert len(all_violations) >= 1
        assert codes(all_violations) == ["CAPACITY_EXCEEDED"]

        # SERVICE robot should be assigned to yield
        assert "r2" in robot_violations
        assert len(robot_violations["r2"]) >= 1

    def test_no_violations_for_non_overlapping_plans(self):
        """No violations when plans don't conflict."""
        r1 = make_plan("r1", [("wait", "room_a")])
        r2 = make_plan("r2", [("wait", "room_b")])

        all_violations, robot_violations = check_cross_plan_conflicts(
            [r1, r2], WORLD, margin=0.0
        )

        assert len(all_violations) == 0
        assert len(robot_violations) == 0

    def test_three_robot_conflict(self):
        """Three robots in capacity-1 region creates conflict."""
        plans = [make_plan(f"r{i}", [("wait", "aisle_1")]) for i in range(3)]

        all_violations, robot_violations = check_cross_plan_conflicts(
            plans, WORLD, margin=0.0
        )

        assert len(all_violations) == 1
        # Capacity 1 keeps one robot; the other two are sent back to replan
        assert len(robot_violations) == 2
        for violations in robot_violations.values():
            assert violations == all_violations

    def test_both_halves_of_a_swap_pick_the_same_yielder(self):
        """Entry time is taken once per robot for the whole round. Taken per
        conflict, each robot would be 'first' in the region it starts in, each
        half of the swap would send the other robot back, and the two resulting
        orderings would contradict each other."""
        r1 = make_plan("r1", [("wait", "door_1"), ("navigate", "aisle_1")], step_s=20.0)
        # r1: door_1 [0, 20), then aisle_1
        r2 = make_plan("r2", [("navigate", "door_2"), ("wait", "aisle_1"), ("navigate", "door_1")])
        # r2: door_2 [0, 10), aisle_1 [10, 20), then door_1

        all_violations, robot_violations = check_cross_plan_conflicts([r1, r2], WORLD, margin=0.5)

        assert codes(all_violations) == ["EDGE_SWAP", "EDGE_SWAP"]
        # r2 reaches its first conflict region (aisle_1, t=10) after r1 reaches
        # its (door_1, t=0), so r2 yields in both halves.
        assert list(robot_violations) == ["r2"]
        assert robot_violations["r2"] == all_violations
