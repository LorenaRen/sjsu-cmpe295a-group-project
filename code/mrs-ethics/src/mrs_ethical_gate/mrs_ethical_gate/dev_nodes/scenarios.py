"""Scenario files for the fake planner: the TeamPlan it starts from (pure Python, no ROS).

  description: what the scenario is about
  map: samples_world          # the world name of the map it is written for
  plan: {assignments: ...}    # a TeamPlan (mrs_common.plan_schema)
  sample: keep_clear_stop.json   # or, instead of plan, one of mrs_common's sample plans

Unknown keys are an error. What to expect is not in the file: the tests say that
(test_scenarios.py runs every scenario of this package against the gate's session).

A `scenario` parameter names a file (a path: it has a "/" or ends in .yaml) or one of this
package's scenarios (a bare name, looked up in the scenarios/ directory). The fake planner
refuses a scenario written for another map: its node names would not exist there, and every
plan would come back unknown_target. It can check only its own map; replan_loop.launch.py
gives the gate the same one.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from mrs_common import load_sample
from mrs_common.plan_schema import TeamPlan, plan_from_json
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ScenarioError(ValueError):
    """A scenario that cannot be run; the message says what is wrong."""


@dataclass(frozen=True)
class Scenario:
    name: str            # the file name without .yaml
    path: Path
    description: str
    map: str             # the world name of the map it is written for
    plan: TeamPlan


class _ScenarioFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = ""
    map: str = Field(min_length=1)
    plan: TeamPlan | None = None
    sample: str | None = None

    @model_validator(mode="after")
    def _one_plan(self) -> "_ScenarioFile":
        if (self.plan is None) == (self.sample is None):
            raise ValueError("give either plan or sample")
        return self


def load_scenario(scenario: str, scenarios_dir: Path) -> Scenario:
    """The scenario a `scenario` parameter names (a path, or a name in scenarios_dir)."""
    path = _path(scenario, scenarios_dir)
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as e:
        raise ScenarioError(f"scenario {path} is not YAML: {e}") from e
    try:
        f = _ScenarioFile.model_validate(data if data is not None else {})
    except ValidationError as e:
        raise ScenarioError(f"scenario {path}: {_first_error(e)}") from e
    plan = f.plan
    if f.sample is not None:
        try:
            plan = plan_from_json(load_sample(f.sample))
        except FileNotFoundError as e:
            raise ScenarioError(f"scenario {path}: sample {f.sample!r} is not one of mrs_common's samples") from e
        except ValidationError as e:
            raise ScenarioError(f"scenario {path}: sample {f.sample}: {_first_error(e)}") from e
    return Scenario(name=path.stem, path=path, description=f.description.strip(), map=f.map, plan=plan)


def check_map(scenario: Scenario, map_name: str) -> None:
    """Raise ScenarioError if the scenario is written for another map than map_name."""
    if scenario.map != map_name:
        raise ScenarioError(f"scenario {scenario.name} is written for map {scenario.map!r}, not {map_name!r}: "
                            "its node names would not exist there")


def scenario_names(scenarios_dir: Path) -> list[str]:
    return sorted(p.stem for p in Path(scenarios_dir).glob("*.yaml"))


def _path(scenario: str, scenarios_dir: Path) -> Path:
    there = ", ".join(scenario_names(scenarios_dir)) or "none"
    if not scenario:
        raise ScenarioError(f"no scenario given: name one of this package's ({there}) or a file")
    if "/" in scenario or scenario.endswith((".yaml", ".yml")):
        path = Path(scenario).expanduser()
    else:
        path = Path(scenarios_dir) / f"{scenario}.yaml"
    if not path.is_file():
        raise ScenarioError(f"scenario {scenario!r} not found ({path}); this package has: {there}")
    return path.resolve()


def _first_error(e: ValidationError) -> str:
    """The first problem, located: 'plan.assignments.0.tasks.0.action: Input should be ...'."""
    errors = e.errors()
    where = ".".join(str(p) for p in errors[0]["loc"])
    text = f"{where}: {errors[0]['msg']}" if where else errors[0]["msg"]
    if len(errors) > 1:
        text += f" (and {len(errors) - 1} more error{'s' if len(errors) > 2 else ''})"
    return text
