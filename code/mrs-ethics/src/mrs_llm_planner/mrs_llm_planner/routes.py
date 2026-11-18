"""Where the team plan sends each robot, and where routes collide (Module 4). Pure Python.

Routes are a fact of the map: a robot always drives the shortest path to its next target. So the
agents are shown each robot's route (route_lines), not just its targets: a small model cannot
work out from a node list that two robots will meet in the aisle.

With conflict_hints on (a setting), the agents are also told where two robots cross the same
capacity-1 zone at the same time (conflict_lines), timed as the gate times tasks: a wait task
drives first and waits afterwards, so its depends_on does not hold the robot back. That is rule S-1 checked for
the agents in advance, so it makes the gate catch less: keep it switchable, and say in an
experiment's write-up whether it was on.

  graph = Graph(map_dict, node_zones)       # map_dict: WorldMap.to_prompt_dict()
  legs = graph.legs(part, start)            # [(task_id, [nodes...]), ...]
"""
from __future__ import annotations

import heapq
import math
import re
from typing import Mapping, Optional, Sequence

_AT = re.compile(r"\b(robot_[a-z]) at ([A-Za-z0-9_]+(?:->[A-Za-z0-9_]+)?)")


def positions_from_text(text: str) -> dict[str, str]:
    """robot -> node from the coordinator's 'Robots now' line ('a->b' edges give b)."""
    out = {}
    for line in text.splitlines():
        if line.startswith("Robots now"):
            for robot, at in _AT.findall(line):
                out[robot] = at.split("->")[-1]
    return out


class Graph:
    def __init__(self, map_dict: Mapping, node_zones: Optional[Mapping[str, Sequence[str]]] = None):
        self.pos = {n: tuple(p) for n, p in map_dict.get("nodes", {}).items()}
        self.adj: dict[str, list[tuple[str, float]]] = {n: [] for n in self.pos}
        for e in map_dict.get("edges", []):
            a, _, b = e.partition("--")
            if a in self.pos and b in self.pos:
                d = math.dist(self.pos[a], self.pos[b])
                self.adj[a].append((b, d))
                self.adj[b].append((a, d))
        self.node_zones = {n: tuple(z) for n, z in (node_zones or {}).items()}
        self.zones = {z["id"]: z for z in map_dict.get("zones", []) if isinstance(z, dict) and "id" in z}

    def path(self, start: str, goal: str) -> list[str]:
        """Shortest path (Dijkstra, Euclidean edge lengths); [] if unreachable or unknown."""
        if start not in self.pos or goal not in self.pos:
            return []
        dist, prev, todo = {start: 0.0}, {}, [(0.0, start)]
        while todo:
            d, n = heapq.heappop(todo)
            if n == goal:
                break
            if d > dist[n]:
                continue
            for m, w in self.adj[n]:
                if d + w < dist.get(m, math.inf):
                    dist[m], prev[m] = d + w, n
                    heapq.heappush(todo, (d + w, m))
        if goal not in dist:
            return []
        out = [goal]
        while out[-1] != start:
            out.append(prev[out[-1]])
        return out[::-1]

    def legs(self, part: Optional[dict], start: Optional[str]) -> list[tuple[str, list[str]]]:
        """Each task's path, in order, from where the robot is."""
        out, here = [], start
        for t in (part or {}).get("tasks", []):
            target = t.get("target")
            p = self.path(here, target) if here else []
            out.append((t.get("task_id"), p or ([target] if target else [])))
            here = target or here
        return out

    def zones_on(self, nodes: Sequence[str]) -> list[str]:
        """Zones passed through, in order, without repeats."""
        seen = []
        for n in nodes:
            for z in self.node_zones.get(n, ()):
                if z not in seen:
                    seen.append(z)
        return seen

    def limited(self, zone: str) -> Optional[int]:
        """The zone's capacity, if it has one."""
        cap = self.zones.get(zone, {}).get("capacity")
        return int(cap) if cap else None


def route_lines(graph: Graph, parts: Mapping[str, dict], starts: Mapping[str, str], team: Sequence[str]) -> list[str]:
    lines = []
    for r in team:
        if r not in parts:
            continue
        for task_id, nodes in graph.legs(parts[r], starts.get(r)):
            narrow = [f"{z} ({graph.zones[z].get('type', 'zone')}, capacity {graph.limited(z)})"
                      for z in graph.zones_on(nodes) if graph.limited(z)]
            cross = f"   crosses {', '.join(narrow)}" if narrow else ""
            lines.append(f"  {r} {task_id}: {' -> '.join(nodes)}{cross}")
    return lines


def _leaves_after(parts: Mapping[str, dict]) -> dict[str, set]:
    """task -> every task certainly done before this task's robot starts driving it.

    As the gate's compiler times it (fleet_checker/adapters/compiler.py): a navigate or
    hold_position leaves when the robot's previous task and every depends_on are done; a WAIT
    leaves when the previous task is done, without waiting for its depends_on (it drives to its
    target first, then waits there). A task is done only after its depends_on are done."""
    info = {}
    for p in parts.values():
        prev = None
        for t in p.get("tasks", []):
            tid = t.get("task_id")
            info[tid] = (prev, list(t.get("depends_on") or []), t.get("action"))
            prev = tid
    done_memo: dict[str, set] = {}

    def done(tid: str, stack: tuple = ()) -> set:
        """tid and everything certainly done when tid is done."""
        if tid in done_memo:
            return done_memo[tid]
        if tid in stack or tid not in info:
            return {tid}
        prev, deps, _ = info[tid]
        acc = {tid}
        for d in ([prev] if prev else []) + deps:
            acc |= done(d, stack + (tid,))
        done_memo[tid] = acc
        return acc

    out = {}
    for tid, (prev, deps, action) in info.items():
        acc = set(done(prev)) if prev else set()
        if action != "wait":
            for d in deps:
                acc |= done(d)
        out[tid] = acc
    return out


def conflict_lines(graph: Graph, parts: Mapping[str, dict], starts: Mapping[str, str], team: Sequence[str]) -> list[str]:
    """Pairs of tasks of different robots that cross the same capacity-1 zone, unordered."""
    crossing = []   # (robot, task_id, zone)
    for r in team:
        if r in parts:
            for task_id, nodes in graph.legs(parts[r], starts.get(r)):
                for z in graph.zones_on(nodes):
                    if graph.limited(z) == 1:
                        crossing.append((r, task_id, z))
    before = _leaves_after(parts)
    out = []
    for i, (r1, t1, z1) in enumerate(crossing):
        for r2, t2, z2 in crossing[i + 1:]:
            if r1 == r2 or z1 != z2:
                continue
            if t1 in before.get(t2, set()) or t2 in before.get(t1, set()):
                continue
            out.append(f"  CONFLICT: {r1}'s {t1} and {r2}'s {t2} both cross {z1} (capacity 1) at the same time. "
                       f"One robot must stay where it is until the other is through: give the later robot's "
                       f"NAVIGATE task a depends_on on the other's task ({t1} or {t2}). A wait task does not help "
                       f"here: it drives to its target first and only then waits.")
    return out


def node_zones(world_map) -> dict[str, list[str]]:
    """node -> ids of the zones it lies in (mrs_sim WorldMap)."""
    return {n: world_map.zones_at(p.x, p.y) for n, p in world_map.nodes.items()}
