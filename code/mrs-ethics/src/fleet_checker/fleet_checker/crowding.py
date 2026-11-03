"""S-2 human crowding: when each robot is within R metres of each person.

S-2 is S-1 over a different resource. The surroundings of a person (the disc of
radius R around them) may hold at most N robots at once, as a region holds at most
its capacity, so cross_plan.check_crowding runs S-1's sweep line on them and R-1
decides who yields. Only the claims are new, and they are worked out here: when is
a robot inside a disc.

A step's path (PlanStep.path) is driven at constant speed from t_start to t_end. On
one straight piece of it the robot's position is linear in time, so "within R of the
person" is a quadratic inequality in time; its two roots, cut to the piece's time
span, say exactly when the robot is inside. Nothing is sampled, so a robot passing
quickly cannot fall between two samples.

    piece from a (at ta) to b (at tb):  p(s) = a + s d,  d = b - a,  s = (t - ta) / (tb - ta)
    |p(s) - h|^2 <= R^2  <=>  |d|^2 s^2 + 2 (e.d) s + (|e|^2 - R^2) <= 0,  e = a - h

A robot standing still is inside or outside for the whole piece. The disc is closed
(distance <= R), but touching its edge for an instant is not a stay. As with S-1's
claims(): margin widens each claim on both sides, for the timing error of the compiled
timeline (R is what absorbs position error), and with hold_final a robot whose plan
ends inside the disc stays there, to +inf.

People are taken to stand where they were observed (WorldSnapshot.humans) for the
whole plan. Distance is a straight line, through walls: a robot on the other side of
a wall counts as near the person, which errs towards reporting (rules_v1.md, S-2).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import Iterator, Sequence

from .plan import TIME_EPS, Claim, HumanObservation, Plan, PlanStep

Point = tuple[float, float]


@dataclass(frozen=True)
class CrowdingLimit:
    """S-2's two numbers: at most `max_robots` robots within `radius` metres of one person.

    No defaults: both are placeholders until they are measured (rules_v1.md, S-2).
    """
    radius: float     # R [m]
    max_robots: int   # N

    def __post_init__(self):
        if not (math.isfinite(self.radius) and self.radius > 0):
            raise ValueError(f"radius must be a positive number of metres, got {self.radius}")
        if not isinstance(self.max_robots, int) or self.max_robots < 1:
            raise ValueError(f"max_robots must be a whole number of at least 1, got {self.max_robots}")


def time_within(a: Point, b: Point, ta: float, tb: float, centre: Point,
                radius: float) -> tuple[float, float] | None:
    """When a robot going straight from a (at time ta) to b (at time tb) is within
    `radius` of `centre`: (t_in, t_out), or None if it never is, or only for an instant."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    ex, ey = a[0] - centre[0], a[1] - centre[1]
    dd = dx * dx + dy * dy
    c = ex * ex + ey * ey - radius * radius
    if dd == 0.0:                    # standing still: inside or outside the whole time
        if c > 0.0:
            return None
        t_in, t_out = ta, tb
    else:
        half_b = ex * dx + ey * dy
        disc = half_b * half_b - dd * c
        if disc <= 0.0:              # misses the disc, or only touches its edge
            return None
        root = math.sqrt(disc)
        s_in = max((-half_b - root) / dd, 0.0)
        s_out = min((-half_b + root) / dd, 1.0)
        t_in, t_out = ta + s_in * (tb - ta), ta + s_out * (tb - ta)
    if t_out - t_in <= TIME_EPS:     # the disc is beyond this piece, or touched for an instant
        return None
    return t_in, t_out


def crowding_claims(plan: Plan, people: Sequence[HumanObservation], radius: float,
                    margin: float = 0.0, hold_final: bool = True) -> Iterator[Claim]:
    """One Claim(person id, robot, step, start, end) for every stretch of a step that the
    robot spends within `radius` of a person, person by person.

    A robot that leaves a person's surroundings and comes back within one step gets two
    claims for that step. The plan's steps must have paths (the compiler adds them).
    """
    if margin < 0:
        raise ValueError("margin must be >= 0")
    if not plan.has_paths:
        raise ValueError(f"plan {plan.plan_id!r} of {plan.robot_id} has no paths, so S-2 cannot "
                         f"tell where the robot is; plans from the compiler have them")
    last = len(plan.steps) - 1
    for person in people:
        for i, step in enumerate(plan.steps):
            for t_in, t_out in _stays(step, person.position, radius):
                held = hold_final and i == last and t_out >= step.t_end - TIME_EPS
                yield Claim(person.human_id, plan.robot_id, i, t_in - margin,
                            math.inf if held else t_out + margin)


def _stays(step: PlanStep, centre: Point, radius: float) -> list[tuple[float, float]]:
    """The stretches of one step during which the robot is within radius of centre."""
    out: list[tuple[float, float]] = []
    for a, b, ta, tb in _pieces(step):
        span = time_within(a, b, ta, tb, centre, radius)
        if span is None:
            continue
        if out and span[0] <= out[-1][1] + TIME_EPS:   # carries on round a corner of the path
            out[-1] = (out[-1][0], max(out[-1][1], span[1]))
        else:
            out.append(span)
    return out


def _pieces(step: PlanStep) -> Iterator[tuple[Point, Point, float, float]]:
    """The straight pieces of a step's path, with their time spans at the step's constant speed."""
    path = step.path
    lengths = [math.dist(p, q) for p, q in pairwise(path)]
    total = sum(lengths)
    if total == 0.0:                 # one point, or several in one place: standing still
        yield path[0], path[0], step.t_start, step.t_end
        return
    duration = step.t_end - step.t_start
    t, driven = step.t_start, 0.0
    for k, ((p, q), length) in enumerate(zip(pairwise(path), lengths)):
        driven += length
        t_next = step.t_end if k == len(lengths) - 1 else step.t_start + duration * driven / total
        yield p, q, t, t_next
        t = t_next
