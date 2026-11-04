"""Gate settings: parameter values + the policy file -> GateSettings, checked before the node starts."""
import json
from pathlib import Path

import pytest
import yaml

from fleet_checker.adapters.team_check import NO_ENTRY_TO_RESTRICTED
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
    assert (s.speed, s.margin, s.max_duration, s.starvation_threshold) == (0.5, 0.5, DEFAULT_MAX_DURATION, 3)


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


def test_team_state_start_source_is_not_built_yet():
    with pytest.raises(SettingsError, match="team_state is not built yet"):
        load(start_source="team_state")


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
