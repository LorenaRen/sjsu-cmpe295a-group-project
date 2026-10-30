import json
import math
from itertools import combinations

import pytest
from pydantic import ValidationError

from checker.plan import (
    Plan, PlanStep, Region, RegionKind, Resource, WorldMap, WorldSnapshot,
    claims, parse_plan, validate_plan,
)

# room_a -- door_1 -- aisle_1 -- door_2 -- room_b,  charger_1 off room_a
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


def make_plan(robot_id, path, step_s=10.0, t0=0.0, **overrides):
    """path: list of (action, region) pairs; each step lasts step_s seconds."""
    steps = [
        {"action": a, "region": r, "t_start": t0 + i * step_s, "t_end": t0 + (i + 1) * step_s}
        for i, (a, r) in enumerate(path)
    ]
    data = {"plan_id": f"{robot_id}-p1", "robot_id": robot_id,
            "task_type": "delivery", "steps": steps} | overrides
    return Plan.model_validate(data)


def codes(violations):
    return [v.code for v in violations]


# ---------------- schema (no map needed) ----------------

def test_parses_planner_json():
    raw = json.dumps({
        "plan_id": "r1-p1", "robot_id": "r1", "task_type": "delivery",
        "steps": [
            {"action": "navigate", "region": "room_a", "t_start": 0, "t_end": 5},
            {"action": "navigate", "region": "door_1", "t_start": 5, "t_end": 8},
        ],
    })
    plan, errors = parse_plan(raw)
    assert errors == []
    assert plan.t_start == 0 and plan.t_end == 8


def test_invented_field_is_rejected():
    # The proposer may not set its own priority; priority is decided by policy.
    plan, errors = parse_plan({
        "plan_id": "r1-p1", "robot_id": "r1", "task_type": "delivery", "priority": 99,
        "steps": [{"action": "wait", "region": "room_a", "t_start": 0, "t_end": 1}],
    })
    assert plan is None
    assert codes(errors) == ["SCHEMA_INVALID"]
    assert "priority" in errors[0].message


def test_unknown_action_is_rejected():
    plan, errors = parse_plan({
        "plan_id": "r1-p1", "robot_id": "r1", "task_type": "delivery",
        "steps": [{"action": "teleport", "region": "room_a", "t_start": 0, "t_end": 1}],
    })
    assert plan is None
    assert set(codes(errors)) == {"SCHEMA_INVALID"}
    assert errors[0].message.startswith("steps.0.action")


def test_zero_length_step_is_rejected():
    with pytest.raises(ValidationError, match="must be greater"):
        PlanStep(action="wait", region="room_a", t_start=3, t_end=3)


def test_gap_between_steps_is_rejected():
    with pytest.raises(ValidationError, match="gap between step 0"):
        Plan.model_validate({
            "plan_id": "p", "robot_id": "r1", "task_type": "service",
            "steps": [
                {"action": "wait", "region": "room_a", "t_start": 0, "t_end": 5},
                {"action": "navigate", "region": "door_1", "t_start": 6, "t_end": 8},
            ],
        })


def test_overlap_between_steps_is_rejected():
    with pytest.raises(ValidationError, match="overlap between step 0"):
        Plan.model_validate({
            "plan_id": "p", "robot_id": "r1", "task_type": "service",
            "steps": [
                {"action": "wait", "region": "room_a", "t_start": 0, "t_end": 5},
                {"action": "navigate", "region": "door_1", "t_start": 4, "t_end": 8},
            ],
        })


def test_world_map_rejects_one_way_adjacency():
    with pytest.raises(ValidationError, match="not symmetric"):
        WorldMap(regions={
            "a": Region(id="a", kind="aisle", capacity=1, center=(0, 0), neighbors=frozenset({"b"})),
            "b": Region(id="b", kind="aisle", capacity=1, center=(1, 0)),
        })


# ---------------- semantic (needs the map) ----------------

def test_valid_route_has_no_violations():
    plan = make_plan("r1", [("navigate", "room_a"), ("navigate", "door_1"),
                            ("navigate", "aisle_1"), ("wait", "aisle_1")])
    assert validate_plan(plan, WORLD) == []


def test_unknown_region_and_resource():
    plan = Plan.model_validate({
        "plan_id": "p", "robot_id": "r1", "task_type": "delivery",
        "steps": [
            {"action": "navigate", "region": "room_a", "t_start": 0, "t_end": 5},
            {"action": "navigate", "region": "aisle_9", "t_start": 5, "t_end": 9,
             "holds": ["freight_lift"]},
        ],
    })
    violations = validate_plan(plan, WORLD)
    assert codes(violations) == ["UNKNOWN_REGION", "UNKNOWN_RESOURCE"]
    assert violations[0].refs[0].step_index == 1


def test_skipping_a_region_is_not_adjacent():
    plan = make_plan("r1", [("navigate", "room_a"), ("navigate", "aisle_1")])
    violations = validate_plan(plan, WORLD)
    assert codes(violations) == ["NOT_ADJACENT"]
    assert [r.step_index for r in violations[0].refs] == [0, 1]


def test_plan_must_start_where_robot_is_and_not_in_the_past():
    plan = make_plan("r1", [("navigate", "door_1"), ("navigate", "aisle_1")])
    snap = WorldSnapshot(time=5.0, robot_regions={"r1": "room_a"})
    assert codes(validate_plan(plan, WORLD, snap)) == ["START_MISMATCH", "STARTS_IN_PAST"]


# ---------------- occupancy view ----------------

def test_claims_include_held_resources_and_final_parking():
    plan = make_plan("r1", [("wait", "room_a"), ("dock", "charger_1")])
    plan = plan.model_copy(update={"steps": (
        plan.steps[0].model_copy(update={"holds": frozenset({"elevator_call"})}),
        plan.steps[1],
    )})
    cs = list(claims(plan))
    assert ("elevator_call", 0.0, 10.0) in [(c.resource_id, c.start, c.end) for c in cs]
    assert cs[-1].resource_id == "charger_1" and cs[-1].end == math.inf
    assert list(claims(plan, hold_final=False))[-1].end == 20.0


def test_margin_widens_intervals():
    plan = make_plan("r1", [("wait", "room_a"), ("navigate", "door_1")])
    first = next(claims(plan, margin=0.5))
    assert (first.start, first.end) == (-0.5, 10.5)


def _shared_regions(*plans, margin):
    """Regions two different robots claim at overlapping times (capacity ignored)."""
    all_claims = [c for p in plans for c in claims(p, margin=margin)]
    return {
        a.resource_id
        for a, b in combinations(all_claims, 2)
        if a.robot_id != b.robot_id and a.resource_id == b.resource_id
        and a.start < b.end and b.start < a.end
    }


# Two robots swap aisle_1 <-> door_2 head-on at t=10.
R1_SWAP = make_plan("r1", [("navigate", "aisle_1"), ("navigate", "door_2")])
R2_SWAP = make_plan("r2", [("navigate", "door_2"), ("navigate", "aisle_1")])


def test_swap_is_invisible_without_margin():
    # Half-open intervals hand each region over at exactly t=10, so nothing overlaps.
    assert _shared_regions(R1_SWAP, R2_SWAP, margin=0.0) == set()


def test_swap_is_visible_with_margin():
    assert _shared_regions(R1_SWAP, R2_SWAP, margin=0.5) == {"aisle_1", "door_2"}


def test_parked_robot_blocks_later_traffic():
    # r1 finishes in aisle_1 at t=20 and stays; r2 passes through aisle_1 at t=30.
    r1 = make_plan("r1", [("navigate", "door_1"), ("wait", "aisle_1")])
    r2 = make_plan("r2", [("navigate", "door_2"), ("navigate", "aisle_1")], t0=20.0)
    assert "aisle_1" in _shared_regions(r1, r2, margin=0.0)
