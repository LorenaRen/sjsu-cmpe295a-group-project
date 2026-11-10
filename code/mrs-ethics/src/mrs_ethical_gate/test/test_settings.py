"""Gate settings: parameter values + the policy file -> GateSettings, checked before the node starts."""
import json
from pathlib import Path

import pytest
import yaml

from fleet_checker.adapters.team_check import NO_ENTRY_TO_RESTRICTED, Rules
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation, RegionKind, TaskType
from fleet_checker.policy import DEFAULT_MAX_DURATION, AccessPolicy
from mrs_ethical_gate.settings import PARAMETERS, REQUIRED, SettingsError, load_settings

PKG = Path(__file__).resolve().parents[1]
SRC = PKG.parent
DEFAULT_POLICY = PKG / "config" / "policy.yaml"
DEFAULT_PEOPLE = PKG / "config" / "people.yaml"
GATE_YAML = PKG / "config" / "gate.yaml"
SAMPLES_WORLD = SRC / "fleet_checker/test/fixtures/samples_world.yaml"
NARROW_AISLE = SRC / "mrs_sim/worlds/narrow_aisle.yaml"


def load(**changes):
    """Every parameter at its declared default, the required ones at the values of
    config/gate.yaml (speed 0.5, robot_length 0.45, S-2's R 1.5 m and N 1), then the changes."""
    values = {name: (None if default is REQUIRED else default) for name, (_, default, _) in PARAMETERS.items()}
    values.update(speed=0.5, robot_length=0.45, crowding_radius=1.5, crowding_max_robots=1)
    values.update(changes)
    return load_settings(values, default_policy_file=DEFAULT_POLICY, default_people_file=DEFAULT_PEOPLE)


def policy_file(tmp_path, text: str) -> str:
    path = tmp_path / "policy.yaml"
    path.write_text(text)
    return str(path)


def people_file(tmp_path, text: str) -> str:
    path = tmp_path / "people.yaml"
    path.write_text(text)
    return str(path)


def test_defaults_check_narrow_aisle_with_two_robots():
    s = load()
    assert s.map_path == NARROW_AISLE
    assert s.robots == ("robot_a", "robot_b")
    assert s.start_source == "spawns"
    assert (s.speed, s.margin, s.max_duration, s.starvation_threshold) == (0.5, 0.5, DEFAULT_MAX_DURATION, 2)


def test_speed_has_no_default():
    assert PARAMETERS["speed"][1] is REQUIRED
    with pytest.raises(SettingsError, match="speed is not set"):
        load(speed=None)


def test_robot_length_has_no_default():
    assert PARAMETERS["robot_length"][1] is REQUIRED
    with pytest.raises(SettingsError, match="robot_length is not set"):
        load(robot_length=None)
    assert load().robot_length == 0.45


def test_shipped_config_sets_the_robot_length_as_a_placeholder():
    text = GATE_YAML.read_text()
    assert yaml.safe_load(text)["mrs_ethical_gate"]["ros__parameters"]["robot_length"] == 0.45
    assert "PLACEHOLDER" in text[text.index("# S-4"):text.index("robot_length:")]


def test_shipped_policy_keeps_every_robot_out_of_restricted_zones():
    s = load()
    assert s.policy_file == DEFAULT_POLICY
    assert s.access_policy == NO_ENTRY_TO_RESTRICTED
    assert s.task_types == {}


def test_map_file_is_used_instead_of_world():
    assert load(map_file=str(SAMPLES_WORLD)).map_path == SAMPLES_WORLD


@pytest.mark.parametrize("name, value", [
    ("speed", 0.0), ("robot_length", 0.0), ("margin", -0.1), ("max_duration", 0.0), ("starvation_threshold", 0),
    ("robots", 0), ("robots", 27), ("crowding_radius", 0.0), ("crowding_max_robots", 0),
])
def test_values_out_of_range_are_refused(name, value):
    with pytest.raises(SettingsError, match=name):
        load(**{name: value})


def test_team_state_takes_positions_no_older_than_its_age_limit():
    s = load(start_source="team_state", team_state_max_age=1.0)
    assert (s.start_source, s.team_state_max_age) == ("team_state", 1.0)
    assert PARAMETERS["team_state_max_age"][1] is REQUIRED
    with pytest.raises(SettingsError, match="team_state_max_age is not set"):
        load(start_source="team_state")
    for bad in (0.0, -1.0, float("inf")):
        with pytest.raises(SettingsError, match="team_state_max_age must be"):
            load(start_source="team_state", team_state_max_age=bad)
    assert load().team_state_max_age is None     # spawns: not used


def test_shipped_config_sets_the_age_limit_and_the_margin_it_needs():
    text = GATE_YAML.read_text()
    params = yaml.safe_load(text)["mrs_ethical_gate"]["ros__parameters"]
    assert (params["start_source"], params["team_state_max_age"]) == ("spawns", 1.0)
    assert "PLACEHOLDER" in text[text.index("# start_source team_state"):text.index("team_state_max_age:")]
    # The error budget of team_state positions, next to the margin it is for.
    assert "at least 3 s" in text[text.index("# Each occupancy interval"):text.index("margin:")]


def test_unknown_start_source_is_refused():
    with pytest.raises(SettingsError, match="start_source"):
        load(start_source="odom")


def test_unknown_world_and_missing_map_file_are_refused(tmp_path):
    with pytest.raises(SettingsError, match="no_such_world"):
        load(world="no_such_world")
    with pytest.raises(SettingsError, match="does not exist"):
        load(map_file=str(tmp_path / "missing.yaml"))


def test_policy_file_sets_both_tables_and_task_types(tmp_path):
    s = load(policy_file=policy_file(tmp_path, (
        "by_kind:\n  restricted: [service]\n"
        "by_id:\n  office: []\n"
        "task_types:\n  robot_b: service\n")))
    assert s.access_policy == AccessPolicy(by_kind={RegionKind.RESTRICTED: frozenset({TaskType.SERVICE})},
                                           by_id={"office": frozenset()})
    assert s.task_types == {"robot_b": TaskType.SERVICE}


def test_policy_file_with_an_unknown_key_is_refused(tmp_path):
    with pytest.raises(SettingsError, match="by_kinds"):
        load(policy_file=policy_file(tmp_path, "by_kinds:\n  restricted: []\n"))


def test_policy_file_with_an_unknown_zone_kind_is_refused(tmp_path):
    with pytest.raises(SettingsError, match="hallway"):
        load(policy_file=policy_file(tmp_path, "by_kind:\n  hallway: []\n"))


def test_task_types_of_robots_outside_the_fleet_are_refused(tmp_path):
    with pytest.raises(SettingsError, match="robot_z"):
        load(policy_file=policy_file(tmp_path, "task_types:\n  robot_z: service\n"))


def test_describe_is_plain_json_for_the_start_up_log():
    d = json.loads(json.dumps(load().describe()))
    assert d["robots"] == ["robot_a", "robot_b"]
    assert d["access_policy"] == {"by_kind": {"restricted": []}, "by_id": {}}
    assert d["crowding"] == {"radius": 1.5, "max_robots": 1}
    assert d["robot_length"] == 0.45
    assert d["people"] == []
    assert d["team_state_max_age"] is None


# ---------------- S-2: R, N and the people ----------------

@pytest.mark.parametrize("name", ["crowding_radius", "crowding_max_robots"])
def test_crowding_limit_has_no_default(name):
    assert PARAMETERS[name][1] is REQUIRED
    with pytest.raises(SettingsError, match=f"{name} is not set"):
        load(**{name: None})


def test_shipped_config_sets_r_and_n_as_placeholders():
    text = GATE_YAML.read_text()
    params = yaml.safe_load(text)["mrs_ethical_gate"]["ros__parameters"]
    assert (params["crowding_radius"], params["crowding_max_robots"]) == (1.5, 1)
    assert "PLACEHOLDER" in text[text.index("# S-2"):text.index("crowding_radius:")]
    assert load().crowding == CrowdingLimit(radius=1.5, max_robots=1)


def test_shipped_people_file_has_nobody():
    s = load()
    assert (s.people_file, s.people) == (DEFAULT_PEOPLE, ())


def test_people_file_lists_people_where_they_stand(tmp_path):
    s = load(people_file=people_file(tmp_path, "people:\n  - {id: person_1, x: 10.0, y: -1.0}\n"))
    assert s.people == (HumanObservation(human_id="person_1", position=(10.0, -1.0)),)
    assert json.loads(json.dumps(s.describe()))["people"] == [{"id": "person_1", "x": 10.0, "y": -1.0}]


def test_people_file_with_two_people_of_one_id_is_refused(tmp_path):
    with pytest.raises(SettingsError, match="person_1"):
        load(people_file=people_file(tmp_path, "people:\n  - {id: person_1, x: 0, y: 0}\n"
                                               "  - {id: person_1, x: 1, y: 0}\n"))


def test_people_file_with_an_unknown_key_is_refused(tmp_path):
    with pytest.raises(SettingsError, match="name"):
        load(people_file=people_file(tmp_path, "people:\n  - {name: person_1, x: 0, y: 0}\n"))


def test_shipped_config_sets_the_starvation_threshold_below_module_4s_attempt_cap():
    params = yaml.safe_load(GATE_YAML.read_text())["mrs_ethical_gate"]["ros__parameters"]
    assert params["starvation_threshold"] == 2 == PARAMETERS["starvation_threshold"][1]


# ---------------- which rules the gate runs ----------------

# Every rule and the fairness ledger on, as the tests of what each rule does need (the gate's
# defaults, in PARAMETERS and config/gate.yaml, run fewer).
ALL_ON = {"rule_s1": True, "rule_s2": True, "rule_s4": True, "rule_p1": True, "rule_p2": True, "rule_p3": True,
          "fairness_ledger": True}
RULE_PARAMETERS = tuple(ALL_ON)


def shipped(name="gate.yaml"):
    return yaml.safe_load((PKG / "config" / name).read_text())["mrs_ethical_gate"]["ros__parameters"]


def test_by_default_the_gate_runs_the_implementation_guides_rules():
    # On: P-1, P-2, S-1 (R-1 by its tie-breaks). Off: S-2, S-4, P-3 and the fairness ledger.
    s = load()
    assert s.rules == Rules(s1=True, s2=False, s4=False, p1=True, p2=True, p3=False)
    assert s.fairness_ledger is False
    assert {n: shipped()[n] for n in RULE_PARAMETERS} == {n: PARAMETERS[n][1] for n in RULE_PARAMETERS}


def test_a_rule_switch_is_true_or_false():
    assert all(PARAMETERS[n][0] is bool for n in RULE_PARAMETERS)
    with pytest.raises(SettingsError, match="rule_s4 must be true or false"):
        load(rule_s4="yes")
    assert load(**ALL_ON).rules == Rules() and load(**ALL_ON).fairness_ledger


def test_the_settings_say_which_rules_run():
    s = load()
    assert s.describe()["rules"] == {"on": ["S-1", "P-1", "P-2"], "off": ["S-2", "S-4", "P-3"],
                                     "fairness_ledger": False}
    assert s.rules_line() == ("rules on: S-1, P-1, P-2; off: S-2, S-4, P-3; fairness ledger off (R-1 ranks by "
                              "task type, then first arrival, then robot id); grounding and compile errors are "
                              "always checked")
    assert load(**ALL_ON).rules_line().startswith("rules on: S-1, S-2, S-4, P-1, P-2, P-3; off: none; fairness "
                                                  "ledger on;")


def test_rules_all_turns_every_rule_and_the_ledger_on():
    assert shipped("rules_all.yaml") == ALL_ON


def test_the_per_robot_baseline_is_the_default_without_the_cross_robot_rules():
    baseline, cross_robot = shipped("rules_per_robot_baseline.yaml"), ("rule_s1", "rule_s2", "rule_s4")
    assert set(baseline) == set(RULE_PARAMETERS)
    assert all(baseline[n] is False for n in cross_robot)
    assert {n: baseline[n] for n in RULE_PARAMETERS if n not in cross_robot} == \
        {n: PARAMETERS[n][1] for n in RULE_PARAMETERS if n not in cross_robot}
    assert load(**baseline).rules == Rules(s1=False, s2=False, s4=False, p1=True, p2=True, p3=False)


def test_shipped_speed_is_module_6s_starting_u_max_as_a_placeholder():
    text = (PKG / "config" / "gate.yaml").read_text()
    assert shipped()["speed"] == 0.25
    about = text[text.index("# Nominal driving speed"):text.index("speed:")]
    assert "PLACEHOLDER" in about and "u_max" in about
