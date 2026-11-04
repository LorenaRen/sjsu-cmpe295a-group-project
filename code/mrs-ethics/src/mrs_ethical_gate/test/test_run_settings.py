"""run_settings: the fake planner node's parameters -> what one run needs (no ROS)."""
import json
from pathlib import Path

import pytest

from mrs_ethical_gate.dev_nodes.run_settings import PARAMETERS, load_run
from mrs_ethical_gate.dev_nodes.scenarios import ScenarioError

PKG = Path(__file__).resolve().parents[1]
SCENARIOS = PKG / "scenarios"
SAMPLES_WORLD = PKG.parent / "fleet_checker/test/fixtures/samples_world.yaml"


def values(**overrides):
    """Every parameter at its default, the scenario s1_passage_no_order on samples_world."""
    v = {name: default for name, (_, default, _) in PARAMETERS.items()}
    return v | {"scenario": "s1_passage_no_order", "map_file": str(SAMPLES_WORLD)} | overrides


def run(**overrides):
    return load_run(values(**overrides), scenarios_dir=SCENARIOS)


def test_defaults():
    r = run()
    assert (r.scenario.name, r.sim_map.name, r.map_path) == ("s1_passage_no_order", "samples_world", SAMPLES_WORLD)
    assert r.start_nodes == {"robot_a": "west_s", "robot_b": "east_n"}      # the fleet at its spawn slots
    assert (r.max_attempts, r.gate_wait, r.verdict_timeout, r.summary_file) == (5, 10.0, 10.0, None)
    assert r.episode_id.startswith("fake-planner-s1-passage-no-order-")


def test_every_parameter_has_a_type_a_default_and_a_description():
    for name, (kind, default, description) in PARAMETERS.items():
        assert isinstance(default, kind) and description, name


def test_a_run_says_what_it_runs_in_plain_json():
    r = run(episode_id="ep-1")
    d = r.describe()
    assert json.loads(json.dumps(d)) == d
    assert (d["scenario"], d["map"], d["fleet"], d["episode_id"]) == (
        "s1_passage_no_order", str(SAMPLES_WORLD), {"robot_a": "west_s", "robot_b": "east_n"}, "ep-1")


def test_a_scenario_is_required():
    with pytest.raises(ScenarioError, match="no scenario given"):
        run(scenario="")


def test_without_a_map_file_the_world_is_used():
    # The default world is narrow_aisle, and the scenario is written for samples_world.
    with pytest.raises(ScenarioError, match="written for map 'samples_world', not 'narrow_aisle'"):
        run(map_file="")


def test_a_map_file_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(ScenarioError, match="map_file .* does not exist"):
        run(map_file=str(tmp_path / "nowhere.yaml"))


def test_an_unknown_world_is_refused():
    with pytest.raises(ScenarioError, match="world"):
        run(map_file="", world="no_such_world")


@pytest.mark.parametrize("robots, problem", [(0, "between 1 and 26"), (27, "between 1 and 26"),
                                             (3, "2 spawn slots")])
def test_the_fleet_must_fit_the_map(robots, problem):
    with pytest.raises(ScenarioError, match=problem):
        run(robots=robots)


def test_the_plan_may_give_tasks_only_to_robots_of_the_fleet():
    with pytest.raises(ScenarioError, match=r"robot_b.*not in the fleet"):
        run(robots=1)


@pytest.mark.parametrize("name, value", [("max_attempts", 0), ("gate_wait", 0.0), ("verdict_timeout", -1.0)])
def test_limits_must_be_positive(name, value):
    with pytest.raises(ScenarioError, match=name):
        run(**{name: value})


def test_a_summary_file_goes_into_a_directory_that_exists(tmp_path):
    assert run(summary_file=str(tmp_path / "summary.json")).summary_file == (tmp_path / "summary.json").resolve()
    with pytest.raises(ScenarioError, match="summary_file"):
        run(summary_file=str(tmp_path / "missing" / "summary.json"))
