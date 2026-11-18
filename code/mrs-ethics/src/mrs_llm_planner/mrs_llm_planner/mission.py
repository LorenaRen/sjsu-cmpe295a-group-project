"""Missions with goals the code can check (Module 4). Pure Python.

/mrs/mission stays a std_msgs/String. Two forms are accepted:

  robot_a: go to room_e. robot_b: go to room_w.                    the line form
  {"text": "...", "goals": {"robot_a": "room_e", "robot_b": "room_w"}}   JSON, for scripted runs

parse_mission() turns either into a Mission(text, goals). A goal names a room zone or a map node:

  room zone (type room, e.g. room_e)   the robot must end at any node inside that room
  node (e.g. room_e_n)                 the robot must end at exactly that node

A name that is both (room_e is the room and the node at its centre) is the room. A goal naming no
robot of the team, or neither a room nor a node, refuses the mission (MissionError). A text with no
"<robot>: go to <place>" is a mission without goals: planning runs, nothing is checked.

The coordinator puts the goals in its opening prompt as one canonical line,

  Goals: robot_a -> room_e (end at room_e, room_e_n or room_e_s); robot_b -> room_w_n.

which the agents read back with goals_from_text(): every agent checks proposals against exactly
the goals the coordinator parsed, and the LLM sees which nodes count.

check_goals(): a robot with a goal must end its plan there, i.e. its last task's target is one of
the goal's nodes. Robots without a goal are not constrained. This is a validity check like
grounding, not one of the gate's rules.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Mapping

from mrs_common.plan_schema import TeamPlan

MISSION_MISMATCH = "mission_mismatch"   # Fallback cause: an agreed plan misses a goal

_LINE_GOAL = re.compile(r"\b(robot_[a-z])\s*:\s*go\s+to\s+(?:the\s+)?([A-Za-z0-9_]+)", re.IGNORECASE)
_GOALS_LINE = re.compile(r"^Goals: (.*)\.$", re.MULTILINE)
_GOAL_ITEM = re.compile(r"([A-Za-z0-9_]+) -> ([A-Za-z0-9_]+)(?: \(end at ([^)]*)\))?")


class MissionError(ValueError):
    pass


@dataclass(frozen=True)
class Goal:
    place: str                  # what the mission named: a room or a node
    nodes: tuple[str, ...]      # the nodes that count as arrived

    def describe(self) -> str:
        if self.nodes == (self.place,):
            return self.place
        names = list(self.nodes)
        listed = ", ".join(names[:-1]) + f" or {names[-1]}" if len(names) > 1 else names[0]
        return f"{self.place} (end at {listed})"


@dataclass(frozen=True)
class Mission:
    text: str
    goals: dict[str, Goal] = field(default_factory=dict)   # robot -> goal

    def goals_line(self) -> str:
        """'Goals: robot_a -> room_e (end at room_e, room_e_n or room_e_s); ...' (empty without goals)."""
        if not self.goals:
            return ""
        return "Goals: " + "; ".join(f"{r} -> {g.describe()}" for r, g in sorted(self.goals.items())) + "."


def room_nodes(world_map) -> dict[str, tuple[str, ...]]:
    """Room zone id -> the map nodes inside it (mrs_sim WorldMap)."""
    return {z.id: tuple(n for n, p in world_map.nodes.items() if z.rect.contains(p.x, p.y))
            for z in world_map.zones if z.type == "room"}


def parse_mission(raw: str, robot_ids: Iterable[str], graph_nodes: Iterable[str],
                  rooms: Mapping[str, Iterable[str]] = None) -> Mission:
    """`rooms`: room zone id -> its nodes (room_nodes()); without it every goal must be a node."""
    robots, nodes = set(robot_ids), set(graph_nodes)
    rooms = {z: tuple(ns) for z, ns in (rooms or {}).items() if ns}
    raw = raw.strip()
    if not raw:
        raise MissionError("empty mission")
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except ValueError as e:
            raise MissionError(f"mission looks like JSON but is not valid JSON: {e}") from None
        if not isinstance(data, dict) or not isinstance(data.get("goals", {}), dict):
            raise MissionError('JSON mission must be {"text": "...", "goals": {"robot_a": "<room or node>", ...}}')
        pairs = [(str(r), str(p)) for r, p in data.get("goals", {}).items()]
        text = str(data.get("text", "")).strip()
    else:
        text = raw
        pairs = [(r.lower(), p) for r, p in _LINE_GOAL.findall(raw)]
    places: dict[str, str] = {}
    for r, p in pairs:
        if r in places and places[r] != p:
            raise MissionError(f"{r} is given two goals: {places[r]} and {p}")
        places[r] = p
    unknown_r = sorted(set(places) - robots)
    unknown_p = sorted({p for p in places.values() if p not in rooms and p not in nodes})
    if unknown_r:
        raise MissionError(f"mission names robot(s) not in the team: {unknown_r}; team: {sorted(robots)}")
    if unknown_p:
        raise MissionError(f"mission goal(s) are neither a room nor a node of the map: {unknown_p}")
    goals = {r: Goal(p, rooms[p] if p in rooms else (p,)) for r, p in places.items()}
    if not text:
        text = " ".join(f"{r}: go to {g.place}." for r, g in sorted(goals.items()))
    return Mission(text=text, goals=goals)


def goals_from_text(text: str) -> dict[str, Goal]:
    """The goals of a 'Goals: ...' line in the coordinator's opening prompt ({} if none)."""
    m = _GOALS_LINE.search(text)
    if not m:
        return {}
    goals = {}
    for robot, place, listed in _GOAL_ITEM.findall(m.group(1)):
        nodes = tuple(n.strip() for n in re.split(r",| or ", listed) if n.strip()) if listed else (place,)
        goals[robot] = Goal(place, nodes)
    return goals


def check_goals(plan: TeamPlan, goals: Mapping[str, Goal]) -> list[str]:
    """One message per robot whose plan does not end at its goal; [] if all do (or no goals)."""
    ends = {a.robot_id: (a.tasks[-1].target if a.tasks else None) for a in plan.assignments}
    problems = []
    for robot, goal in sorted(goals.items()):
        if robot not in ends:
            problems.append(f"{robot} is missing from the plan: a proposal must give every robot on the team "
                            f"tasks, and {robot}'s last task must end at its mission goal {goal.describe()}")
        elif ends[robot] not in goal.nodes:
            problems.append(f"{robot}'s last task ends at {ends[robot]}, but its mission goal is {goal.describe()}")
    return problems
