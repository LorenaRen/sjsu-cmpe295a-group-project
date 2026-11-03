"""Where each robot is when a plan is checked: the compiler's start_nodes (pure Python).

  spawns       robot i of the fleet at the map's spawn slot i, the way mrs_sim's launch file
               places robots. Right only until the robots move: once Module 6 drives them,
               the gate needs Module 3's /mrs/team_state (start_source team_state, not built).

The whole fleet goes in, robots without tasks too, because a parked robot still blocks
others (compiler.py). And the fleet, not the robots a plan happens to name, decides the
slots: a plan that names only robot_b must still find it at slot 1.
"""
from __future__ import annotations

from typing import Sequence

from mrs_common import names as N

from fleet_checker.adapters.sim_map import SimMap, spawn_starts


def spawn_start_nodes(sim_map: SimMap, robots: Sequence[str]) -> dict[str, str]:
    """robots[i] at spawn slot i. Raises ValueError if the map has fewer slots."""
    return spawn_starts(sim_map, list(robots))


def node_from_at(at: str) -> str:
    """The node a plan starts from, for a WorldState.at value (for the team_state source).

    A robot between two nodes ("a->b") raises for now: the compiler cannot start a plan
    on an edge yet. The intended handling (finish the edge, then plan from b) is in
    rules_v1.md and needs Module 6 to confirm that a robot finishes the edge it is on.
    """
    if not at:
        raise ValueError("WorldState.at is empty: the robot's position is unknown")
    if N.edge_from_at(at) is not None:
        raise ValueError(f"robot is between two nodes ({at}); starting a plan on an edge is not supported yet")
    return at
