"""What one run of the fake planner needs, from the node's parameters (pure Python, no ROS).

world, map_file and robots mean what they mean for the gate, and must have the same values as
the gate's: the planner retargets on that map's graph, and its fallback holds that fleet at
that map's spawn slots. replan_loop.launch.py passes the same values to both nodes.

Every problem is a ScenarioError: the node logs it and does not start.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from mrs_common import names as N
from mrs_common.ids import new_episode_id
from mrs_sim.world_map import resolve_world

from fleet_checker.adapters.sim_map import SimMap, load_sim_map

from ..starts import spawn_start_nodes
from .scenarios import Scenario, ScenarioError, check_map, load_scenario

# name -> (type, default, description)
PARAMETERS: dict[str, tuple[type, Any, str]] = {
    "scenario": (str, "", "a scenario file, or the name of one of this package's scenarios (required)"),
    "world": (str, "narrow_aisle", "the map, as for the gate: a world name in mrs_sim/worlds, or a path to a .sdf"),
    "map_file": (str, "", "the map, as for the gate: a map .yaml to use instead of world"),
    "robots": (int, 2, "fleet size, as for the gate: robot_a, robot_b, ... at the map's spawn slots"),
    "max_attempts": (int, 5, "plans to send before falling back; a placeholder until Module 4's cap is known"),
    "gate_wait": (float, 10.0, "seconds to wait for the gate before attempt 0"),
    "verdict_timeout": (float, 10.0, "seconds to wait for each verdict"),
    "summary_file": (str, "", "where to write the summary as JSON; empty: nowhere (it is logged anyway)"),
    "episode_id": (str, "", "empty: a new one for every run, so that the gate starts with an empty ledger"),
}


@dataclass(frozen=True)
class RunSettings:
    scenario: Scenario
    sim_map: SimMap
    map_path: Path
    start_nodes: dict[str, str]      # the whole fleet, at its spawn slots
    max_attempts: int
    gate_wait: float                 # [s]
    verdict_timeout: float           # [s]
    summary_file: Path | None
    episode_id: str

    def describe(self) -> dict[str, Any]:
        """Plain JSON values, for the node's start-up log line."""
        return {"scenario": self.scenario.name, "scenario_file": str(self.scenario.path),
                "map": str(self.map_path), "fleet": dict(self.start_nodes), "max_attempts": self.max_attempts,
                "gate_wait": self.gate_wait, "verdict_timeout": self.verdict_timeout,
                "summary_file": str(self.summary_file) if self.summary_file else None,
                "episode_id": self.episode_id}


def load_run(values: Mapping[str, Any], *, scenarios_dir: Path) -> RunSettings:
    """values: every parameter of PARAMETERS. Raises ScenarioError for anything that cannot run."""
    scenario = load_scenario(values["scenario"], scenarios_dir)
    map_path = _map_path(values["world"], values["map_file"])
    try:
        sim_map = load_sim_map(map_path)
    except (OSError, ValueError) as e:   # MapError and pydantic's ValidationError are ValueErrors
        raise ScenarioError(f"map {map_path}: {e}") from e
    check_map(scenario, sim_map.name)

    _require(1 <= values["robots"] <= 26, f"robots must be between 1 and 26, got {values['robots']}")
    fleet = N.robot_ids(values["robots"])
    try:
        start_nodes = spawn_start_nodes(sim_map, fleet)
    except ValueError as e:
        raise ScenarioError(str(e)) from e
    outside = [r for r in scenario.plan.robot_ids if r not in start_nodes]
    _require(not outside, f"scenario {scenario.name} gives tasks to {', '.join(outside)}, not in the fleet "
                          f"({', '.join(fleet)}): set robots")

    _require(values["max_attempts"] >= 1, f"max_attempts must be at least 1, got {values['max_attempts']}")
    for name in ("gate_wait", "verdict_timeout"):
        _require(values[name] > 0, f"{name} must be more than 0 s, got {values[name]}")
    summary_file = None
    if values["summary_file"]:
        summary_file = Path(values["summary_file"]).expanduser().resolve()
        _require(summary_file.parent.is_dir(), f"summary_file {summary_file}: no directory {summary_file.parent}")

    return RunSettings(scenario=scenario, sim_map=sim_map, map_path=map_path, start_nodes=start_nodes,
                       max_attempts=values["max_attempts"], gate_wait=float(values["gate_wait"]),
                       verdict_timeout=float(values["verdict_timeout"]), summary_file=summary_file,
                       episode_id=values["episode_id"] or new_episode_id(f"fake planner {scenario.name}"))


def _map_path(world: str, map_file: str) -> Path:
    """The gate's choice (its settings.py): map_file if given, else the map of the world."""
    if map_file:
        path = Path(map_file).expanduser().resolve()
        _require(path.is_file(), f"map_file {path} does not exist")
        return path
    try:
        return resolve_world(world)[1]
    except FileNotFoundError as e:
        raise ScenarioError(f"world: {e}") from e


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise ScenarioError(message)
