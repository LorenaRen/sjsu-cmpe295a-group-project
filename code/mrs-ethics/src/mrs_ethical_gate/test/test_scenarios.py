"""scenarios: the files the fake planner starts from, and the scenarios this package ships."""
from pathlib import Path

import pytest
from mrs_common import load_sample, plan_from_json
from mrs_common import reasons as R

from mrs_ethical_gate.dev_nodes.scenarios import ScenarioError, check_map, load_scenario, scenario_names

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"

PLAN = """\
plan:
  assignments:
    - robot_id: robot_a
      tasks:
        - {task_id: a1, action: hold_position, target: room_3, priority: 1}
"""


def write(tmp_path, text: str) -> Path:
    path = tmp_path / "mine.yaml"
    path.write_text(text)
    return path


def test_a_scenario_is_found_by_name():
    s = load_scenario("s1_passage_no_order", SCENARIOS)
    assert (s.name, s.map, s.path) == ("s1_passage_no_order", "samples_world", SCENARIOS / "s1_passage_no_order.yaml")
    assert s.description.startswith("Both robots drive through the one-robot passage")
    assert [t.task_id for a in s.plan.assignments for t in a.tasks] == ["a1", "a2", "b1", "b2"]


def test_a_scenario_is_found_by_path(tmp_path):
    path = write(tmp_path, "map: samples_world\n" + PLAN)
    s = load_scenario(str(path), SCENARIOS)
    assert (s.name, s.path, s.description) == ("mine", path, "")
    assert s.plan.task("a1").target == "room_3"


def test_a_scenario_can_use_a_sample_of_mrs_common():
    s = load_scenario("p1_keep_clear_stop", SCENARIOS)
    assert s.plan == plan_from_json(load_sample("keep_clear_stop.json"))


@pytest.mark.parametrize("text", ["map: samples_world\nsample: keep_clear_stop.json\n" + PLAN,
                                  "map: samples_world\n"])
def test_a_scenario_has_either_a_plan_or_a_sample(tmp_path, text):
    with pytest.raises(ScenarioError, match="either plan or sample"):
        load_scenario(str(write(tmp_path, text)), SCENARIOS)


def test_a_scenario_says_which_map_it_is_for(tmp_path):
    with pytest.raises(ScenarioError, match="map: Field required"):
        load_scenario(str(write(tmp_path, PLAN)), SCENARIOS)


def test_unknown_fields_are_refused(tmp_path):
    # e.g. an expected outcome: the tests say what to expect, not the scenario files
    with pytest.raises(ScenarioError, match="expect: Extra inputs are not permitted"):
        load_scenario(str(write(tmp_path, "map: samples_world\nexpect: approved\n" + PLAN)), SCENARIOS)


def test_an_invalid_plan_says_where_it_is_wrong(tmp_path):
    text = "map: samples_world\n" + PLAN.replace("hold_position", "teleport")
    with pytest.raises(ScenarioError, match=r"plan\.assignments\.0\.tasks\.0\.action: Input should be"):
        load_scenario(str(write(tmp_path, text)), SCENARIOS)


@pytest.mark.parametrize("sample, problem", [("no_such_sample.json", "not one of mrs_common's samples"),
                                             ("invalid_bad_action.json", "assignments.0.tasks.0.action")])
def test_a_sample_must_be_a_valid_plan_of_mrs_common(tmp_path, sample, problem):
    with pytest.raises(ScenarioError, match=problem):
        load_scenario(str(write(tmp_path, f"map: samples_world\nsample: {sample}\n")), SCENARIOS)


def test_a_file_that_is_not_yaml_is_refused(tmp_path):
    with pytest.raises(ScenarioError, match="not YAML"):
        load_scenario(str(write(tmp_path, "map: [samples_world\n")), SCENARIOS)


def test_an_unknown_scenario_lists_the_ones_there_are():
    with pytest.raises(ScenarioError, match="no_such_scenario.*s1_passage_no_order"):
        load_scenario("no_such_scenario", SCENARIOS)


def test_no_scenario_at_all_is_refused():
    with pytest.raises(ScenarioError, match="no scenario given"):
        load_scenario("", SCENARIOS)


def test_a_scenario_for_another_map_is_refused():
    s = load_scenario("s1_passage_no_order", SCENARIOS)
    check_map(s, "samples_world")
    with pytest.raises(ScenarioError, match="written for map 'samples_world', not 'narrow_aisle'"):
        check_map(s, "narrow_aisle")


# ---------------- the scenarios this package ships ----------------

SHIPPED = {   # name -> (outcome, round it passed in, rule_ids of each round)
    "s1_passage_no_order": ("approved", 2, [[R.CAPACITY_EXCEEDED], []]),
    "s4_both_hold_room_3": ("stuck", None, [[R.NODE_OCCUPIED]]),
    "p1_keep_clear_stop": ("approved", 3, [[R.FINAL_STOP_IN_PASSTHROUGH], [R.REGION_ACCESS_DENIED], []]),
    "p2_restricted_target": ("approved", 3, [[R.REGION_ACCESS_DENIED], [R.CAPACITY_EXCEEDED], []]),
}


def test_every_scenario_of_the_package_is_tested_here():
    assert scenario_names(SCENARIOS) == sorted(SHIPPED)


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_each_scenario_of_the_package_against_the_gate(run_with_gate, samples_world, name):
    s = load_scenario(name, SCENARIOS)
    check_map(s, samples_world.name)
    f, done = run_with_gate(s.plan)
    outcome, passed_in_round, rule_ids = SHIPPED[name]
    summary = f.summary()
    assert (done.outcome, summary["passed_in_round"]) == (outcome, passed_in_round)
    assert [r["rule_ids"] for r in f.rounds] == rule_ids
