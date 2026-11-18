"""Planner settings: the nodes' ROS parameters, checked (pure Python).

Both nodes read the same parameter file (config/planner.yaml); each uses what it needs. Settings are
read once at start-up: changing the model or the temperature halfway through an episode would make
its results incomparable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from mrs_common import names as N

from .guidelines import parse_rules
from .llm_client import BACKENDS, LLMConfig


class SettingsError(ValueError):
    pass


# name -> (type, default, description). The nodes declare these, read-only.
PARAMETERS: dict[str, tuple[type, Any, str]] = {
    "robot_id": (str, "", "agent node only: the robot this agent plans for, e.g. robot_a"),
    "robots": (int, 2, "fleet size: robot_a, robot_b, ... (as in sim.launch.py and the gate)"),
    "world": (str, "narrow_aisle", "mrs_sim world name, or a path to a .sdf with its .yaml next to it"),
    "map_file": (str, "", "a map .yaml to use instead of world"),
    "rules": (str, "S-1,P-1,P-2", "rules the agents are told; must be the rules the gate runs"),
    "backend": (str, "fake", f"LLM backend: {', '.join(BACKENDS)}"),
    "model": (str, "", "model name for the backend"),
    "base_url": (str, "", "API base URL; required for openai_compatible (e.g. a vLLM server on HPC)"),
    "api_key_env": (str, "", "environment variable with the API key; empty: the backend's default"),
    "secrets_file": (str, "", "KEY=VALUE file read into the environment at start-up; empty: $MRS_SECRETS or .env"),
    "llm_timeout_s": (float, 60.0, "wall-clock limit of one LLM request [s]"),
    "llm_max_retries": (int, 3, "retries for rate limits, 5xx and connection errors"),
    "max_tokens": (int, 2000, "longest LLM answer [tokens]"),
    "num_ctx": (int, 8192, "backend ollama: context window [tokens], prompt + answer"),
    "temperature": (float, 0.7, "sampling temperature, fixed for an experiment"),
    "constrained_output": (bool, True, "make the LLM follow the reply's JSON schema while generating (ollama, openai, openai_compatible)"),
    "max_repairs": (int, 1, "repair calls per turn for a reply that cannot be used"),
    "conflict_hints": (bool, True, "tell the agents where two robots' routes cross a capacity-1 zone unordered (rule S-1 checked for them in advance: the gate then catches less)"),
    "repair_temperature": (float, 0.2, "sampling temperature of repair calls"),
    "fake_script_file": (str, "", "backend fake: JSON file {robot_id: [reply, ...]}; empty: every reply is discussion"),
    "log_file": (str, "llm_log.sqlite", "SQLite file of every LLM request and response"),
    "run_id": (str, "dev", "run name in the log"),
    "episode_id": (str, "", "coordinator: episode id; empty: a new one from mrs_common.ids"),
    "max_rounds": (int, 3, "rounds of the dialogue before no_agreement (then every robot holds)"),
    "turn_timeout_s": (float, 150.0, "coordinator: deadline for one agent turn [s], wall clock (catches a crashed agent)"),
    "cycle_timeout_s": (float, 300.0, "coordinator: hard limit for one whole planning cycle [s], wall clock"),
    "verdict_timeout_s": (float, 30.0, "coordinator: deadline for the gate's verdict [s], wall clock"),
    "pause_world": (bool, False, "coordinator: pause the Gazebo world while planning"),
    "world_control_service": (str, "", "coordinator: ros_gz ControlWorld service; empty: /world/<world>/control"),
}


@dataclass(frozen=True)
class PlannerSettings:
    robot_id: str
    team: tuple[str, ...]
    world: str
    map_file: str
    rules: tuple[str, ...]
    llm: LLMConfig
    secrets_file: str
    temperature: float
    constrained_output: bool
    max_repairs: int
    conflict_hints: bool
    repair_temperature: float
    fake_script: Optional[list]
    log_file: str
    run_id: str
    episode_id: str
    max_rounds: int
    turn_timeout_s: float
    cycle_timeout_s: float
    verdict_timeout_s: float
    pause_world: bool
    world_control_service: str

    def describe(self) -> dict[str, Any]:
        return {"robot_id": self.robot_id, "team": list(self.team), "world": self.map_file or self.world,
                "rules": list(self.rules), "model": self.llm.model_name, "temperature": self.temperature,
                "max_rounds": self.max_rounds, "constrained_output": self.constrained_output,
                "max_repairs": self.max_repairs, "conflict_hints": self.conflict_hints,
                "cycle_timeout_s": self.cycle_timeout_s,
                "log_file": self.log_file, "run_id": self.run_id}


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise SettingsError(message)


def load_settings(values: Mapping[str, Any], *, agent: bool) -> PlannerSettings:
    """Check the parameter values. `agent`: for an agent node, which needs robot_id."""
    v = {name: values.get(name, default) for name, (_, default, _) in PARAMETERS.items()}
    _require(1 <= v["robots"] <= 26, f"robots must be between 1 and 26, got {v['robots']}")
    team = tuple(N.robot_ids(v["robots"]))
    if agent:
        _require(v["robot_id"] in team, f"robot_id must be one of {list(team)}, got {v['robot_id']!r}")
    try:
        rules = parse_rules(v["rules"])
        llm = LLMConfig(backend=v["backend"], model=v["model"], base_url=v["base_url"],
                        api_key_env=v["api_key_env"], timeout_s=v["llm_timeout_s"],
                        max_retries=v["llm_max_retries"], max_tokens=v["max_tokens"], num_ctx=v["num_ctx"])
    except ValueError as e:
        raise SettingsError(str(e)) from e
    _require(0.0 <= v["temperature"] <= 2.0, f"temperature must be in [0, 2], got {v['temperature']}")
    _require(v["max_rounds"] >= 1, "max_rounds must be >= 1")
    _require(v["turn_timeout_s"] > 0 and v["verdict_timeout_s"] > 0, "timeouts must be > 0")
    _require(0 <= v["max_repairs"] <= 3, f"max_repairs must be 0..3, got {v['max_repairs']}")
    _require(0.0 <= v["repair_temperature"] <= 2.0, "repair_temperature must be in [0, 2]")
    calls = 1 + v["max_repairs"]
    _require(v["turn_timeout_s"] > v["llm_timeout_s"] * calls,
             f"turn_timeout_s ({v['turn_timeout_s']}) must exceed a whole turn: {calls} LLM calls "
             f"x llm_timeout_s = {calls * v['llm_timeout_s']}")
    _require(v["cycle_timeout_s"] > v["turn_timeout_s"],
             f"cycle_timeout_s ({v['cycle_timeout_s']}) must exceed turn_timeout_s ({v['turn_timeout_s']})")
    _require(bool(v["run_id"]), "run_id must not be empty")

    fake_script = None
    if v["fake_script_file"]:
        _require(v["backend"] == "fake", "fake_script_file is only for backend fake")
        fake_script = _read_fake_script(Path(v["fake_script_file"]).expanduser(),
                                        v["robot_id"] if agent else None, team)
    return PlannerSettings(
        robot_id=v["robot_id"] if agent else "", team=team, world=v["world"], map_file=v["map_file"],
        rules=rules, llm=llm, secrets_file=v["secrets_file"], temperature=float(v["temperature"]),
        constrained_output=bool(v["constrained_output"]), max_repairs=int(v["max_repairs"]),
        conflict_hints=bool(v["conflict_hints"]),
        repair_temperature=float(v["repair_temperature"]),
        fake_script=fake_script, log_file=v["log_file"], run_id=v["run_id"], episode_id=v["episode_id"],
        max_rounds=v["max_rounds"], turn_timeout_s=float(v["turn_timeout_s"]),
        cycle_timeout_s=float(v["cycle_timeout_s"]),
        verdict_timeout_s=float(v["verdict_timeout_s"]), pause_world=bool(v["pause_world"]),
        world_control_service=v["world_control_service"])


def _read_fake_script(path: Path, robot_id: Optional[str], team: tuple[str, ...]) -> Optional[list]:
    _require(path.is_file(), f"fake_script_file {path} does not exist")
    try:
        data = json.loads(path.read_text())
    except ValueError as e:
        raise SettingsError(f"fake_script_file {path}: {e}") from e
    if isinstance(data, dict):
        data = {k: v for k, v in data.items() if not k.startswith("_")}   # "_comment" and the like
    _require(isinstance(data, dict) and all(isinstance(x, list) and x for x in data.values()),
             f"fake_script_file {path} must be an object {{robot_id: [reply, ...]}}")
    unknown = sorted(set(data) - set(team))
    _require(not unknown, f"fake_script_file {path} names robots outside the fleet: {unknown}")
    if robot_id is None or robot_id not in data:
        return None
    # a reply may be written as a JSON object for readability; the LLM would send text
    return [r if isinstance(r, str) else json.dumps(r) for r in data[robot_id]]


def load_map(settings: PlannerSettings):
    """(WorldMap, spawn nodes per robot) for the settings' world or map_file."""
    import yaml
    from mrs_sim.world_map import WorldMap, load_world_map

    try:
        if settings.map_file:
            wm = WorldMap.model_validate(yaml.safe_load(Path(settings.map_file).expanduser().read_text()))
        else:
            wm, _ = load_world_map(settings.world)
    except (OSError, ValueError) as e:
        raise SettingsError(f"map: {e}") from e
    _require(len(wm.spawns) >= len(settings.team),
             f"map {wm.world!r} has {len(wm.spawns)} spawn slots for {len(settings.team)} robots")
    return wm, {r: s.node for r, s in zip(settings.team, wm.spawns)}
