"""Benchmark scenarios: a fleet's start nodes and a TeamPlan, drawn from a seed (pure Python).

Each scenario has its own seed string, f"{MASTER_SEED}/{map}/{robots}/{density}/{index}", for a
random.Random. CPython turns a str seed into an int with SHA-512, and its generator is the same
on every platform, so a seed string gives the same scenario on every run and machine. Drawn in
this order:

  starts   robots distinct nodes of the map's open areas (maps.stoppable_nodes), for robot_00,
           robot_01, ...
  targets  robot by robot: from the other half of the map (maps.halves) with probability
           p_cross, the density, else from its own half; never another robot's target or its
           own start. If the half it drew has no node left, the other half: `crossings` counts
           the robots whose target ended up across.
  tasks    one per robot: hold_position at its target, with no depends_on (adding those is
           what the gate's constraints do).
  people   only in the S-2 series: people near open-area nodes, each a node of the pool plus an
           offset uniform in a disc of radius PERSON_OFFSET (rejection sampling: arithmetic
           only, so the positions are the same on every platform), from the seed
           f"{seed}/people{count}". The plan is the scenario's without people.

The manifest (manifest.csv) has one digest per cell over all its scenarios, so a change to the
generator, the seeds or the maps is caught before results are compared:

    python -m bench.scenarios     # from benchmarks/: write manifest.csv again (only on purpose)
"""
from __future__ import annotations

import csv
import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from mrs_common.plan_schema import TeamPlan

from fleet_checker.adapters.sim_map import SimMap
from fleet_checker.plan import HumanObservation

from . import config
from .maps import halves, stoppable_nodes

Cell = tuple[str, int, str, int]   # (map, robots, density, people)


@dataclass(frozen=True)
class Scenario:
    map_name: str
    n_robots: int
    density: str
    index: int
    seed: str
    starts: dict[str, str]                  # robot -> start node
    plan: TeamPlan
    crossings: int                          # robots whose target is in the other half
    people: tuple[HumanObservation, ...] = field(default=())

    @property
    def cell(self) -> Cell:
        return (self.map_name, self.n_robots, self.density, len(self.people))

    @property
    def scenario_id(self) -> str:
        return f"{self.map_name}/{self.n_robots}/{self.density}/{len(self.people)}/{self.index}"

    def digest(self) -> str:
        """SHA-256 of what the scenario is: start nodes, the plan and the people."""
        what = {"starts": self.starts, "plan": self.plan.model_dump(mode="json"),
                "people": [[p.human_id, p.position[0], p.position[1]] for p in self.people]}
        return hashlib.sha256(json.dumps(what, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def generate(maps: Mapping[str, SimMap], map_name: str, n_robots: int, density: str, index: int,
             people: int = 0) -> Scenario:
    sim_map = maps[map_name]
    pool = stoppable_nodes(sim_map)
    sides = halves(sim_map, pool)
    side_of = {n: k for k, half in enumerate(sides) for n in half}
    seed = f"{config.MASTER_SEED}/{map_name}/{n_robots}/{density}/{index}"
    rng = random.Random(seed)
    p_cross = config.DENSITIES[density]

    robots = [f"robot_{k:02d}" for k in range(n_robots)]
    starts = dict(zip(robots, rng.sample(pool, n_robots)))
    taken: set[str] = set()
    assignments, crossings = [], 0
    for k, (robot, start) in enumerate(starts.items()):
        own = side_of[start]
        wanted = 1 - own if rng.random() < p_cross else own
        target = None
        for side in (wanted, 1 - wanted):
            free = [n for n in sides[side] if n not in taken and n != start]
            if free:
                target = rng.choice(free)
                break
        if target is None:
            raise ValueError(f"{map_name}: no target left for {robot} of {n_robots}")
        taken.add(target)
        crossings += side_of[target] != own
        assignments.append({"robot_id": robot, "tasks": [
            {"task_id": f"t{k:02d}", "action": "hold_position", "target": target, "priority": 1}]})
    plan = TeamPlan.model_validate({"assignments": assignments})
    return Scenario(map_name, n_robots, density, index, seed, starts, plan, crossings,
                    _people(sim_map, pool, f"{seed}/people{people}", people))


def _people(sim_map: SimMap, pool: list[str], seed: str, count: int) -> tuple[HumanObservation, ...]:
    rng = random.Random(seed)
    people = []
    for k in range(count):
        x, y = sim_map.positions[rng.choice(pool)]
        while True:
            u, v = 2 * rng.random() - 1, 2 * rng.random() - 1
            if u * u + v * v <= 1:
                break
        people.append(HumanObservation(human_id=f"person_{k}",
                                       position=(x + config.PERSON_OFFSET * u, y + config.PERSON_OFFSET * v)))
    return tuple(people)


def cells(robots: Mapping[str, Sequence[int]] | None = None) -> list[Cell]:
    """The cells of the main matrix: every map, its fleet sizes (config.ROBOTS unless given) and
    every density; no people."""
    robots = config.ROBOTS if robots is None else robots
    return [(m, n, d, 0) for m, ns in robots.items() for n in ns for d in config.DENSITIES]


def people_cells(robots: Sequence[int] | None = None) -> list[Cell]:
    s = config.PEOPLE_SERIES
    return [(s["map"], n, s["density"], s["people"]) for n in (s["robots"] if robots is None else robots)]


def scenarios_of(maps: Mapping[str, SimMap], cell: Cell, count: int) -> list[Scenario]:
    map_name, n, density, people = cell
    return [generate(maps, map_name, n, density, i, people) for i in range(count)]


def manifest_rows(maps: Mapping[str, SimMap]) -> list[dict[str, str]]:
    rows = []
    for cell in cells() + people_cells():
        digests = "\n".join(s.digest() for s in scenarios_of(maps, cell, config.PERF_SCENARIOS))
        rows.append({"map": cell[0], "robots": str(cell[1]), "density": cell[2], "people": str(cell[3]),
                     "scenarios": str(config.PERF_SCENARIOS),
                     "digest": hashlib.sha256(digests.encode()).hexdigest()})
    return rows


def write_manifest(maps: Mapping[str, SimMap], path: Path) -> None:
    rows = manifest_rows(maps)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def check_manifest(maps: Mapping[str, SimMap], path: Path) -> list[str]:
    """What differs between the scenarios the generator makes now and the manifest; [] if nothing."""
    with open(path, newline="") as f:
        committed = {(r["map"], r["robots"], r["density"], r["people"]): r for r in csv.DictReader(f)}
    problems = []
    for row in manifest_rows(maps):
        key = (row["map"], row["robots"], row["density"], row["people"])
        old = committed.pop(key, None)
        if old is None:
            problems.append(f"cell {'/'.join(key)} is not in {path.name}")
        elif old != row:
            problems.append(f"cell {'/'.join(key)}: the scenarios differ from {path.name}")
    problems += [f"cell {'/'.join(key)} of {path.name} is no longer generated" for key in committed]
    return problems


if __name__ == "__main__":
    from .maps import load_maps
    write_manifest(load_maps(), config.MANIFEST)
    print(f"wrote {config.MANIFEST}")
