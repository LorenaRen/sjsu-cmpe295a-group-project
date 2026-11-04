"""Where each robot is when a plan is checked: the compiler's start nodes (pure Python).

  spawns      FixedStarts: robot i of the fleet at the map's spawn slot i, the way mrs_sim's
              launch file places robots. Right only until the robots move.
  team_state  TeamStateStarts: the latest /mrs/team_state of Module 3, kept as it arrives
              (observe) and read when a plan arrives (at). A robot at a node starts there. A
              robot on the edge "a->b" first drives the rest of it (compiler.OnEdge), and its
              plan starts at b: the executor is assumed to finish the edge it is on (to be
              confirmed with Module 6).

The whole fleet goes in, robots without tasks too, because a parked robot still blocks
others (compiler.py). And the fleet, not the robots a plan happens to name, decides the
slots: a plan that names only robot_b must still find it at slot 1.

When the gate does not know where every robot of the fleet is, it checks nothing (an
UnknownStarts; verdict.unknown_positions_verdict rejects the plan): no snapshot yet, the
snapshot of another episode, a robot missing from it, an `at` the map does not have, a robot
on an edge whose pose is outside every zone of the map, no time stamp, or a position older
than max_age, or stamped more than max_age ahead of the gate's clock, which means the two use
different clocks. A robot's age comes from its WorldState.stamp, or the TeamState's when that
is not set. Robots of the snapshot outside the fleet are left out, and logged.

No position is moved forward: a robot is where the snapshot says, however old that is. The
error this leaves is for the margin to cover (config/gate.yaml, rules_v1.md).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, NamedTuple, Sequence

from mrs_common import names as N

from fleet_checker.adapters.compiler import OnEdge
from fleet_checker.adapters.sim_map import SimMap, spawn_starts

SPAWNS = "spawns"
TEAM_STATE = "team_state"


def spawn_start_nodes(sim_map: SimMap, robots: Sequence[str]) -> dict[str, str]:
    """robots[i] at spawn slot i. Raises ValueError if the map has fewer slots."""
    return spawn_starts(sim_map, list(robots))


# --------------------------------------------------------------------------
# What a start source gives for one plan
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Starts:
    """Where every robot of the fleet starts: check_team_plan's start_nodes and on_edges."""
    start_nodes: dict[str, str]                                # robot -> node its plan starts from
    on_edges: dict[str, OnEdge] = field(default_factory=dict)  # robots between two nodes
    source: str = SPAWNS
    snapshot_age: float | None = None       # [s] the gate's clock minus TeamState.stamp
    ages: dict[str, float] = field(default_factory=dict)       # robot -> age of its position [s]
    left_out: tuple[str, ...] = ()          # robots of the snapshot outside the fleet
    blocked_edges: tuple[str, ...] = ()     # TeamState.blocked_edges: routes do not avoid them yet

    def summary(self) -> dict[str, Any]:
        """Plain JSON values, for the decision log."""
        if self.source == SPAWNS:
            return {"source": SPAWNS}
        robots = {}
        for robot in sorted(self.start_nodes):
            e = self.on_edges.get(robot)
            robots[robot] = {"at": f"{e.from_node}{N.TRAVEL_SEP}{e.to_node}" if e else self.start_nodes[robot],
                             "age_s": round(self.ages[robot], 3)}
        return {"source": self.source, "snapshot_age_s": _rounded(self.snapshot_age), "robots": robots,
                "left_out": list(self.left_out), "blocked_edges_not_used": list(self.blocked_edges)}


@dataclass(frozen=True)
class UnknownStarts:
    """Why the gate does not know where every robot is; the plan is then not checked."""
    problems: tuple[tuple[str | None, str], ...]   # (robot, or None for all of them; why)
    snapshot_age: float | None = None

    def summary(self) -> dict[str, Any]:
        return {"source": TEAM_STATE, "snapshot_age_s": _rounded(self.snapshot_age),
                "unknown": [{"robot": robot, "why": why} for robot, why in self.problems]}


def _rounded(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds, 3)


# --------------------------------------------------------------------------
# spawns
# --------------------------------------------------------------------------

class FixedStarts:
    """Start nodes that do not change (start_source spawns)."""

    def __init__(self, start_nodes: Mapping[str, str]):
        self.fleet = tuple(sorted(start_nodes))
        self._starts = Starts(dict(sorted(start_nodes.items())))

    def at(self, episode_id: str, now: float) -> Starts:
        return self._starts


# --------------------------------------------------------------------------
# team_state
# --------------------------------------------------------------------------

class RobotState(NamedTuple):
    """The fields of one WorldState the gate reads."""
    robot_id: str
    at: str                 # a node, or "a->b" between two nodes
    x: float                # map frame [m]
    y: float
    stamp: float | None     # [s]; None when not set


class TeamSnapshot(NamedTuple):
    """The fields of one TeamState the gate reads."""
    episode_id: str         # "" goes for any episode
    robots: tuple[RobotState, ...]
    stamp: float | None     # [s]; None when not set
    blocked_edges: tuple[str, ...] = ()


def team_snapshot(msg: Any) -> TeamSnapshot:
    """A TeamSnapshot from an mrs_msgs TeamState, or anything with its fields."""
    return TeamSnapshot(
        episode_id=msg.episode_id,
        robots=tuple(RobotState(r.robot_id, r.at, float(r.x), float(r.y), _seconds(r.stamp))
                     for r in msg.robots),
        stamp=_seconds(msg.stamp),
        blocked_edges=tuple(msg.blocked_edges))


def _seconds(stamp: Any) -> float | None:
    """builtin_interfaces/Time [s]; None for 0 s 0 ns, which is what an unset stamp is."""
    if stamp.sec == 0 and stamp.nanosec == 0:
        return None
    return stamp.sec + stamp.nanosec * 1e-9


def start_from(sim_map: SimMap, state: RobotState) -> tuple[str, OnEdge | None]:
    """The node a robot's plan starts from, and the edge it is on (None at a node). Raises
    ValueError, saying why, for an `at` the map does not have, or for a robot on an edge
    whose pose is outside every zone of the map."""
    at = state.at
    if not at:
        raise ValueError("at is empty")
    try:
        edge = N.edge_from_at(at)
    except ValueError as e:
        raise ValueError(f"at {at!r} is not a node or an edge: {e}") from e
    if edge is None:
        if at not in sim_map.positions:
            raise ValueError(f"at {at!r} is not a node of map {sim_map.name!r}")
        return at, None
    a, b = at.split(N.TRAVEL_SEP, 1)
    if (a, b) not in sim_map.pieces:
        raise ValueError(f"at {at!r} is not an edge of map {sim_map.name!r}")
    if not sim_map.zones_at(state.x, state.y):
        raise ValueError(f"it is on {at}, but its pose ({state.x:g}, {state.y:g}) is outside every zone "
                         f"of map {sim_map.name!r}")
    return b, OnEdge(a, b, (state.x, state.y))


class TeamStateStarts:
    """Start nodes from the latest /mrs/team_state (start_source team_state)."""

    def __init__(self, sim_map: SimMap, fleet: Sequence[str], *, max_age: float):
        if not (math.isfinite(max_age) and max_age > 0):
            raise ValueError(f"max_age must be a time > 0 s, got {max_age}")
        self.sim_map = sim_map
        self.fleet = tuple(sorted(fleet))
        self.max_age = max_age
        self.latest: TeamSnapshot | None = None

    def observe(self, snapshot: TeamSnapshot) -> None:
        self.latest = snapshot

    def at(self, episode_id: str, now: float) -> Starts | UnknownStarts:
        """Where the fleet starts, for a plan of episode_id checked at `now` [s] on the gate's clock."""
        s = self.latest
        if s is None:
            return UnknownStarts(((None, f"no {N.TEAM_STATE} has been received yet"),))
        snapshot_age = None if s.stamp is None else now - s.stamp
        if s.episode_id and s.episode_id != episode_id:
            return UnknownStarts(
                ((None, f"the latest {N.TEAM_STATE} is of episode {s.episode_id!r}, not {episode_id!r}"),),
                snapshot_age)
        states = {r.robot_id: r for r in s.robots}
        problems: list[tuple[str | None, str]] = []
        start_nodes: dict[str, str] = {}
        on_edges: dict[str, OnEdge] = {}
        ages: dict[str, float] = {}
        for robot in self.fleet:
            why, age = self._problem(states.get(robot), s, now)
            if why is None:
                try:
                    start_nodes[robot], on_edge = start_from(self.sim_map, states[robot])
                except ValueError as e:
                    why = str(e)
                else:
                    ages[robot] = age
                    if on_edge is not None:
                        on_edges[robot] = on_edge
            if why is not None:
                problems.append((robot, why))
        if problems:
            return UnknownStarts(tuple(problems), snapshot_age)
        return Starts(start_nodes, on_edges, TEAM_STATE, snapshot_age, ages,
                      left_out=tuple(sorted(set(states) - set(self.fleet))), blocked_edges=s.blocked_edges)

    def _problem(self, state: RobotState | None, s: TeamSnapshot, now: float) -> tuple[str | None, float]:
        """Why a robot's position cannot be used (None if it can), and how old it is."""
        if state is None:
            return f"it is not in the latest {N.TEAM_STATE}", math.nan
        stamp = state.stamp if state.stamp is not None else s.stamp
        if stamp is None:
            return "its position has no time stamp", math.nan
        age = now - stamp
        if age > self.max_age:
            return f"its position is {age:.1f} s old, more than team_state_max_age ({self.max_age:g} s)", age
        if age < -self.max_age:
            return (f"its position is stamped {-age:.1f} s ahead of the gate's clock; do the gate and "
                    f"Module 3 use the same clock (use_sim_time)?"), age
        return None, age
