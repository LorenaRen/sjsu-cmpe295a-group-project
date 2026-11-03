"""The margin = 0 fix to sweep_over_capacity changes nothing else.

The fix: a robot whose claims on a resource touch (to 1e-6 s) is present throughout, so a
conflict is no longer cut in two at that robot's handover. sweep_before_the_fix below is the
sweep as it was before (a Counter of robots, ends before starts), copied unchanged. These tests
compare the two:

- every scenario the S-1 and S-2 tests check, compiled, with margin > 0: the same result for
  every region (S-1) and every person (S-2);
- random claims in which no robot's claims touch: the same result. Touching claims are the
  only case the fix is meant to change.
"""
import random
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common.plan_schema import TeamPlan

from conftest import CROSSROADS, TWO_NARROW_ZONES
from fleet_checker.adapters.compiler import compile_team_plan
from fleet_checker.adapters.sim_map import load_sim_map, sim_map_from_dict, spawn_starts
from fleet_checker.cross_plan import OverCapacity, sweep_over_capacity
from fleet_checker.crowding import crowding_claims
from fleet_checker.plan import TIME_EPS, Claim, HumanObservation, claims

SRC = Path(__file__).resolve().parents[2]
SAMPLES_WORLD = Path(__file__).parent / "fixtures" / "samples_world.yaml"
NARROW_AISLE = SRC / "mrs_sim/worlds/narrow_aisle.yaml"
MARGINS = (0.1, 0.5, 1.0)
RADII = (1.0, 1.5, 2.0)


class ConflictEvent(NamedTuple):
    time: float
    is_start: bool
    claim: Claim


def sweep_before_the_fix(claims, capacity):
    """sweep_over_capacity before the fix, unchanged (with the event type it used)."""
    events = []
    for claim in claims:
        events.append(ConflictEvent(claim.start, True, claim))
        events.append(ConflictEvent(claim.end, False, claim))
    events.sort(key=lambda e: (e.time, e.is_start))
    out = []
    active_claims = set()
    active_robots = Counter()
    stretch_start = None
    stretch_claims = set()
    for event in events:
        robot_id = event.claim.robot_id
        if event.is_start:
            active_claims.add(event.claim)
            active_robots[robot_id] += 1
            if len(active_robots) > capacity:
                if stretch_start is None:
                    stretch_start = event.time
                stretch_claims.update(active_claims)
        else:
            active_claims.discard(event.claim)
            active_robots[robot_id] -= 1
            if active_robots[robot_id] == 0:
                del active_robots[robot_id]
            if stretch_start is not None and len(active_robots) <= capacity:
                ordered = tuple(sorted(stretch_claims, key=lambda c: (c.robot_id, c.step_index, c.start, c.end)))
                out.append(OverCapacity(ordered, stretch_start, event.time))
                stretch_start = None
                stretch_claims.clear()
    return out


# ---------------- the scenarios of the S-1 and S-2 tests ----------------

def task(task_id, action, target, depends_on=()):
    return {"task_id": task_id, "action": action, "target": target, "priority": 1,
            "depends_on": list(depends_on)}


def team(*assignments):
    return TeamPlan.model_validate(
        {"assignments": [{"robot_id": r, "tasks": tasks} for r, tasks in assignments]})


def sample(name, drop_dependencies=False):
    plan = plan_from_json(load_sample(name))
    if drop_dependencies:
        data = plan.model_dump(mode="json")
        for a in data["assignments"]:
            for t in a["tasks"]:
                t["depends_on"] = []
        plan = TeamPlan.model_validate(data)
    return plan


def person(x, y):
    return HumanObservation(human_id="person_1", position=(x, y))


SCENARIOS = {
    # name: (map, plans, speed, people)
    "samples_world": (lambda: load_sim_map(SAMPLES_WORLD), [
        sample("valid_two_robots.json"), sample("valid_two_robots.json", drop_dependencies=True),
        sample("keep_clear_stop.json"), sample("restricted_target.json"),
        team(("robot_a", [task("a1", "navigate", "corridor_1", ["b1"])]),
             ("robot_b", [task("b1", "hold_position", "pass_e")])),
        team(("robot_a", [task("a1", "navigate", "pass_w"), task("a2", "navigate", "corridor_1", ["b1"])]),
             ("robot_b", [task("b1", "navigate", "room_2")])),
    ], 0.5, [person(10.0, -1.0), person(5.0, 4.5)]),
    "narrow_aisle": (lambda: load_sim_map(NARROW_AISLE), [
        team(("robot_a", [task("a1", "hold_position", "room_e_s")]),
             ("robot_b", [task("b2", "hold_position", "room_w_n")])),
        team(("robot_a", [task("a1", "hold_position", "room_e_s")]),
             ("robot_b", [task("b1", "wait", "room_e_n", ["a1"]), task("b2", "hold_position", "room_w_n", ["b1"])])),
        team(("robot_a", [task("a1", "wait", "room_w_s", ["b1"]), task("a2", "hold_position", "room_e_s", ["a1"])]),
             ("robot_b", [task("b1", "hold_position", "room_w_n")])),
        team(("robot_a", [task("a1", "hold_position", "room_e_s")]),
             ("robot_b", [task("b1", "hold_position", "room_e_n")])),
    ], 0.5, [person(12.5, 0.5), person(12.0, 1.8)]),
    "crossroads": (lambda: sim_map_from_dict(CROSSROADS), [
        team(("robot_a", [task("a1", "navigate", "e")]), ("robot_b", [task("b1", "navigate", "s")])),
        team(("robot_a", [task("a1", "navigate", "e", ["b1"])]), ("robot_b", [task("b1", "navigate", "s")])),
    ], 1.0, [person(5.0, 1.0)]),
    "two_narrow_zones": (lambda: sim_map_from_dict(TWO_NARROW_ZONES), [
        team(("robot_a", [task("a1", "navigate", "er")]), ("robot_b", [task("b1", "navigate", "wr")])),
    ], 1.0, [person(2.0, 0.0)]),
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_tested_scenario_gives_the_same_result_with_margin(name):
    load, plans, speed, people = SCENARIOS[name]
    sim_map = load()
    compared = 0
    for team_plan in plans:
        compiled = compile_team_plan(team_plan, sim_map, spawn_starts(sim_map, ["robot_a", "robot_b"]),
                                     speed=speed)
        assert not compiled.violations
        for margin in MARGINS:
            for hold_final in (True, False):
                by_region: dict[str, list[Claim]] = {}
                for plan in compiled.plans.values():
                    for c in claims(plan, margin=margin, hold_final=hold_final):
                        by_region.setdefault(c.resource_id, []).append(c)
                for region, cs in by_region.items():
                    for capacity in {sim_map.world.capacity(region), 1}:
                        assert sweep_over_capacity(cs, capacity) == sweep_before_the_fix(cs, capacity)
                        compared += 1
                for p in people:
                    for radius in RADII:
                        cs = [c for plan in compiled.plans.values()
                              for c in crowding_claims(plan, [p], radius, margin, hold_final)]
                        assert sweep_over_capacity(cs, 1) == sweep_before_the_fix(cs, 1)
                        compared += 1
    assert compared > 0


# ---------------- random claims ----------------

def random_claims(rng):
    out = []
    for r in range(rng.randint(1, 5)):
        t = rng.uniform(0, 10)
        for step in range(rng.randint(1, 5)):
            start = t + rng.uniform(-2, 3)          # may overlap the robot's previous claim
            end = start + rng.uniform(0.01, 6)
            if rng.random() < 0.1:
                end = float("inf")
            out.append(Claim("hall", f"r{r}", step, start, end))
            t = max(t, start)
    return out


def touches(cs):
    """Whether a robot has two claims that meet with a gap of at most TIME_EPS."""
    by_robot = {}
    for c in cs:
        by_robot.setdefault(c.robot_id, []).append(c)
    for robot_claims in by_robot.values():
        for a in robot_claims:
            for b in robot_claims:
                if a is not b and 0 <= b.start - a.end <= TIME_EPS:
                    return True
    return False


def test_random_claims_that_do_not_touch_give_the_same_result():
    rng = random.Random(295)
    compared = 0
    while compared < 2000:
        cs = random_claims(rng)
        if touches(cs):
            continue
        capacity = rng.randint(1, 3)
        assert sweep_over_capacity(cs, capacity) == sweep_before_the_fix(cs, capacity), cs
        compared += 1
