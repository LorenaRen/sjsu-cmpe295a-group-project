"""Tests for policy checks (P-1, P-2, P-3)."""
import pytest

from checker.plan import (
    Plan, PlanStep, Region, RegionKind, Resource, TaskType, WorldMap,
)
from checker.policy import (
    AccessPolicy, check_final_stop_in_passthrough, check_max_duration,
    check_region_access, validate_policy,
)


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
                            neighbors=frozenset({"door_2", "patient_room"})),
        "patient_room": Region(id="patient_room", kind=RegionKind.OPEN_AREA, capacity=2, center=(25, 0),
                               neighbors=frozenset({"room_b"})),
    },
    resources={},
)


def make_plan(robot_id: str, path: list, step_s: float = 10.0, t0: float = 0.0,
              task_type: str = "delivery") -> Plan:
    """path: list of (action, region) pairs; each step lasts step_s seconds."""
    steps = [
        {"action": a, "region": r, "t_start": t0 + i * step_s, "t_end": t0 + (i + 1) * step_s}
        for i, (a, r) in enumerate(path)
    ]
    data = {"plan_id": f"{robot_id}-p1", "robot_id": robot_id,
            "task_type": task_type, "steps": steps}
    return Plan.model_validate(data)


def codes(violations):
    return [v.code for v in violations]


# ---------------- P-1: Final stop in passthrough zone ----------------

class TestFinalStopInPassthrough:
    """Tests for P-1."""

    def test_final_stop_in_aisle_rejected(self):
        """Plan ending in AISLE should be rejected."""
        plan = make_plan("r1", [("navigate", "room_a"), ("navigate", "door_1"), ("wait", "aisle_1")])
        violations = check_final_stop_in_passthrough(plan, WORLD)
        assert codes(violations) == ["FINAL_STOP_IN_PASSTHROUGH"]
        assert "aisle" in violations[0].message

    def test_final_stop_in_doorway_rejected(self):
        """Plan ending in DOORWAY should be rejected."""
        plan = make_plan("r1", [("navigate", "room_a"), ("wait", "door_1")])
        violations = check_final_stop_in_passthrough(plan, WORLD)
        assert codes(violations) == ["FINAL_STOP_IN_PASSTHROUGH"]
        assert "doorway" in violations[0].message

    def test_final_stop_in_room_ok(self):
        """Plan ending in OPEN_AREA is fine."""
        plan = make_plan("r1", [("navigate", "door_1"), ("navigate", "room_a")])
        violations = check_final_stop_in_passthrough(plan, WORLD)
        assert violations == []

    def test_final_stop_in_charger_ok(self):
        """Plan ending in CHARGER is fine."""
        plan = make_plan("r1", [("navigate", "room_a"), ("dock", "charger_1")])
        violations = check_final_stop_in_passthrough(plan, WORLD)
        assert violations == []


# ---------------- P-2: Region access restriction ----------------

class TestRegionAccess:
    """Tests for P-2."""

    def test_no_policy_allows_all(self):
        """Without policy, all access is allowed."""
        plan = make_plan("r1", [("navigate", "patient_room")], task_type="delivery")
        violations = check_region_access(plan, WORLD, policy=None)
        assert violations == []

    def test_region_id_restriction(self):
        """Access denied by specific region_id."""
        policy: AccessPolicy = {
            "patient_room": frozenset({TaskType.SERVICE}),  # Only SERVICE allowed
        }
        plan = make_plan("r1", [("navigate", "room_b"), ("navigate", "patient_room")], task_type="delivery")
        violations = check_region_access(plan, WORLD, policy=policy)
        assert codes(violations) == ["REGION_ACCESS_DENIED"]
        assert "patient_room" in violations[0].message

    def test_region_kind_restriction(self):
        """Access denied by region kind."""
        policy: AccessPolicy = {
            "aisle": frozenset({TaskType.DELIVERY}),  # Only DELIVERY can use aisles
        }
        plan = make_plan("r1", [("navigate", "door_1"), ("navigate", "aisle_1")], task_type="service")
        violations = check_region_access(plan, WORLD, policy=policy)
        assert codes(violations) == ["REGION_ACCESS_DENIED"]
        assert "aisle" in violations[0].message

    def test_allowed_task_type_passes(self):
        """Task type in allowed set passes."""
        policy: AccessPolicy = {
            "patient_room": frozenset({TaskType.SERVICE}),
        }
        plan = make_plan("r1", [("navigate", "patient_room")], task_type="service")
        violations = check_region_access(plan, WORLD, policy=policy)
        assert violations == []

    def test_multiple_violations(self):
        """Multiple steps can each generate violations."""
        policy: AccessPolicy = {
            "aisle": frozenset({TaskType.DELIVERY}),
            "doorway": frozenset({TaskType.DELIVERY}),
        }
        # SERVICE robot going through door and aisle
        plan = make_plan("r1", [("navigate", "door_1"), ("navigate", "aisle_1")], task_type="service")
        violations = check_region_access(plan, WORLD, policy=policy)
        assert len(violations) == 2


# ---------------- P-3: Maximum plan duration ----------------

class TestMaxDuration:
    """Tests for P-3."""

    def test_short_plan_ok(self):
        """Plan within duration limit passes."""
        plan = make_plan("r1", [("wait", "room_a")], step_s=100.0)
        violations = check_max_duration(plan, max_duration=3600.0)
        assert violations == []

    def test_long_plan_rejected(self):
        """Plan exceeding duration limit is rejected."""
        plan = make_plan("r1", [("wait", "room_a")], step_s=4000.0)
        violations = check_max_duration(plan, max_duration=3600.0)
        assert codes(violations) == ["MAX_DURATION_EXCEEDED"]
        assert "4000" in violations[0].message

    def test_exact_limit_ok(self):
        """Plan exactly at limit passes."""
        plan = make_plan("r1", [("wait", "room_a")], step_s=3600.0)
        violations = check_max_duration(plan, max_duration=3600.0)
        assert violations == []

    def test_custom_limit(self):
        """Custom duration limit works."""
        plan = make_plan("r1", [("wait", "room_a")], step_s=200.0)
        violations = check_max_duration(plan, max_duration=100.0)
        assert codes(violations) == ["MAX_DURATION_EXCEEDED"]


# ---------------- Combined validate_policy ----------------

class TestValidatePolicy:
    """Tests for combined policy validation."""

    def test_multiple_policy_violations(self):
        """Multiple policy rules can trigger at once."""
        policy: AccessPolicy = {
            "aisle": frozenset({TaskType.DELIVERY}),
        }
        # SERVICE robot ends in aisle (P-1 and P-2 both violated)
        plan = make_plan("r1", [("navigate", "door_1"), ("wait", "aisle_1")], task_type="service")
        violations = validate_policy(plan, WORLD, access_policy=policy, max_duration=3600.0)
        violation_codes = codes(violations)
        assert "FINAL_STOP_IN_PASSTHROUGH" in violation_codes
        assert "REGION_ACCESS_DENIED" in violation_codes

    def test_clean_plan_passes(self):
        """Plan that follows all policies passes."""
        plan = make_plan("r1", [("navigate", "room_a"), ("dock", "charger_1")])
        violations = validate_policy(plan, WORLD)
        assert violations == []
