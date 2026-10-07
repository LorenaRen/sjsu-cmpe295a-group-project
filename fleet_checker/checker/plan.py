"""Plan representation for the shared checking layer.

Three kinds of data are kept apart on purpose:

* Plan          what one robot intends to do. Proposed by its LLM planner,
                with timings filled in by a deterministic compiler. Untrusted.
* WorldMap      static facts: regions, adjacency, capacities, geometry.
* WorldSnapshot facts observed at planning time: where robots and people are.

A proposer controls only its Plan, so it cannot misstate a capacity, invent a
map region, or give itself a higher priority. Every check reads all three.

Time is seconds on the shared simulation clock. Every interval is half-open,
[t_start, t_end), so a robot leaving a region at t=10 and another entering it
at t=10 do not overlap.
"""
from __future__ import annotations

import math
from enum import Enum
from itertools import pairwise
from typing import Any, Iterator, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

TIME_EPS = 1e-6  # tolerance when checking that consecutive steps touch


class _Frozen(BaseModel):
    # frozen: a plan cannot change after it has been checked.
    # extra="forbid": fields the LLM invents are an error, not silently dropped.
    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

class Action(str, Enum):
    NAVIGATE = "navigate"
    PICKUP = "pickup"
    DROPOFF = "dropoff"
    WAIT = "wait"
    DOCK = "dock"


class TaskType(str, Enum):
    DELIVERY = "delivery"
    SERVICE = "service"


class RegionKind(str, Enum):
    AISLE = "aisle"
    DOORWAY = "doorway"
    OPEN_AREA = "open_area"
    CHARGER = "charger"
    ELEVATOR = "elevator"


# --------------------------------------------------------------------------
# World (context the proposer does not control)
# --------------------------------------------------------------------------

class Region(_Frozen):
    id: str = Field(min_length=1)
    kind: RegionKind
    capacity: int = Field(ge=1)          # max robots inside at the same time
    center: tuple[float, float]          # metres, map frame
    neighbors: frozenset[str] = frozenset()


class Resource(_Frozen):
    """An exclusive resource that is not a place, e.g. an elevator call."""
    id: str = Field(min_length=1)
    capacity: int = Field(ge=1)


class WorldMap(_Frozen):
    regions: dict[str, Region]
    resources: dict[str, Resource] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _consistent(self) -> "WorldMap":
        for key, region in self.regions.items():
            if key != region.id:
                raise ValueError(f"region key {key!r} does not match id {region.id!r}")
            for n in region.neighbors:
                if n not in self.regions:
                    raise ValueError(f"region {key!r} lists unknown neighbor {n!r}")
                if key not in self.regions[n].neighbors:
                    raise ValueError(f"adjacency {key!r}-{n!r} is not symmetric")
        for key, res in self.resources.items():
            if key != res.id:
                raise ValueError(f"resource key {key!r} does not match id {res.id!r}")
            if key in self.regions:
                raise ValueError(f"id {key!r} is both a region and a resource")
        return self

    def knows(self, resource_id: str) -> bool:
        return resource_id in self.regions or resource_id in self.resources

    def capacity(self, resource_id: str) -> int:
        if resource_id in self.regions:
            return self.regions[resource_id].capacity
        return self.resources[resource_id].capacity


class HumanObservation(_Frozen):
    human_id: str = Field(min_length=1)
    position: tuple[float, float]        # metres, map frame


class WorldSnapshot(_Frozen):
    time: float = Field(ge=0)
    robot_regions: dict[str, str] = Field(default_factory=dict)   # robot_id -> region now
    humans: tuple[HumanObservation, ...] = ()


# --------------------------------------------------------------------------
# Plan (what the proposer controls)
# --------------------------------------------------------------------------

class PlanStep(_Frozen):
    action: Action
    region: str = Field(min_length=1)    # the one region the robot is in
    t_start: float = Field(ge=0)
    t_end: float
    holds: frozenset[str] = frozenset()  # extra exclusive resources held

    @model_validator(mode="after")
    def _positive_duration(self) -> "PlanStep":
        if not self.t_end > self.t_start:
            raise ValueError(
                f"t_end ({self.t_end}) must be greater than t_start ({self.t_start})")
        return self


class Plan(_Frozen):
    plan_id: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)   # bumped on every replan
    robot_id: str = Field(min_length=1)
    task_type: TaskType
    steps: tuple[PlanStep, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _contiguous(self) -> "Plan":
        # No gaps: the robot is always somewhere. No overlaps: it is in one place.
        for i, (a, b) in enumerate(pairwise(self.steps)):
            if abs(b.t_start - a.t_end) > TIME_EPS:
                kind = "gap" if b.t_start > a.t_end else "overlap"
                raise ValueError(
                    f"{kind} between step {i} (ends {a.t_end}) "
                    f"and step {i + 1} (starts {b.t_start})")
        return self

    @property
    def t_start(self) -> float:
        return self.steps[0].t_start

    @property
    def t_end(self) -> float:
        return self.steps[-1].t_end


# --------------------------------------------------------------------------
# Rejection reasons (shared format for replanning feedback and telemetry)
# --------------------------------------------------------------------------

class StepRef(_Frozen):
    robot_id: str
    step_index: int


class Violation(_Frozen):
    code: str
    message: str
    refs: tuple[StepRef, ...] = ()


def parse_plan(raw: str | bytes | dict[str, Any]) -> tuple[Plan | None, list[Violation]]:
    """Parse planner output. Schema errors come back as Violations, not exceptions,
    so they can be returned to the planner the same way as any other rejection."""
    try:
        if isinstance(raw, (str, bytes)):
            plan = Plan.model_validate_json(raw)
        else:
            plan = Plan.model_validate(raw)
    except ValidationError as e:
        return None, [
            Violation(
                code="SCHEMA_INVALID",
                message=f"{'.'.join(map(str, err['loc'])) or '<root>'}: {err['msg']}")
            for err in e.errors()
        ]
    return plan, []


def validate_plan(plan: Plan, world: WorldMap,
                  snapshot: WorldSnapshot | None = None) -> list[Violation]:
    """Single-plan checks that need the map but not other robots' plans."""
    out: list[Violation] = []

    def at(*indices: int) -> tuple[StepRef, ...]:
        return tuple(StepRef(robot_id=plan.robot_id, step_index=i) for i in indices)

    for i, step in enumerate(plan.steps):
        if step.region not in world.regions:
            out.append(Violation(code="UNKNOWN_REGION", refs=at(i),
                                 message=f"step {i}: region {step.region!r} is not on the map"))
        for r in sorted(step.holds):
            if not world.knows(r):
                out.append(Violation(code="UNKNOWN_RESOURCE", refs=at(i),
                                     message=f"step {i}: resource {r!r} does not exist"))

    for i, (a, b) in enumerate(pairwise(plan.steps)):
        both_known = a.region in world.regions and b.region in world.regions
        if both_known and a.region != b.region and b.region not in world.regions[a.region].neighbors:
            out.append(Violation(code="NOT_ADJACENT", refs=at(i, i + 1),
                                 message=f"steps {i}->{i + 1}: {a.region!r} and "
                                         f"{b.region!r} are not adjacent"))

    if snapshot is not None:
        current = snapshot.robot_regions.get(plan.robot_id)
        if current is not None and current != plan.steps[0].region:
            out.append(Violation(code="START_MISMATCH", refs=at(0),
                                 message=f"plan starts in {plan.steps[0].region!r} but "
                                         f"robot is in {current!r}"))
        if plan.t_start + TIME_EPS < snapshot.time:
            out.append(Violation(code="STARTS_IN_PAST", refs=at(0),
                                 message=f"plan starts at {plan.t_start}, "
                                         f"current time is {snapshot.time}"))
    return out


# --------------------------------------------------------------------------
# Occupancy view: what the cross-robot detectors consume
# --------------------------------------------------------------------------

class Claim(NamedTuple):
    resource_id: str     # a region id or a resource id
    robot_id: str
    step_index: int
    start: float
    end: float           # half-open; math.inf for "stays here after the plan ends"


def claims(plan: Plan, margin: float = 0.0, hold_final: bool = True) -> Iterator[Claim]:
    """Expand a plan into the intervals during which it occupies each region/resource.

    margin: widen each interval by this many seconds on both sides to absorb
        timing error. It also makes a robot briefly occupy both regions while
        crossing a boundary, which is what lets a capacity check see two robots
        swapping places head-on (see the swap tests).
    hold_final: a robot that finishes its plan stays in its last region, so the
        last region claim runs to +inf until a newer plan replaces it.
    """
    if margin < 0:
        raise ValueError("margin must be >= 0")
    last = len(plan.steps) - 1
    for i, step in enumerate(plan.steps):
        start = step.t_start - margin
        step_end = step.t_end + margin
        region_end = math.inf if (hold_final and i == last) else step_end
        yield Claim(step.region, plan.robot_id, i, start, region_end)
        for r in sorted(step.holds):
            yield Claim(r, plan.robot_id, i, start, step_end)
