"""fake_team_state's snapshot files and parameters (pure Python)."""
from pathlib import Path

import pytest

from mrs_ethical_gate.dev_nodes.scenarios import ScenarioError
from mrs_ethical_gate.dev_nodes.snapshots import PARAMETERS, load_feed, load_snapshot, snapshot_names
from mrs_ethical_gate.session import GateSession
from mrs_ethical_gate.starts import RobotState, TeamSnapshot, TeamStateStarts, UnknownStarts

from fleet_checker.adapters.sim_map import load_sim_map

PKG = Path(__file__).resolve().parents[1]
SNAPSHOTS = PKG / "snapshots"
SAMPLES_WORLD = PKG.parent / "fleet_checker/test/fixtures/samples_world.yaml"


def feed(**changes):
    values = {name: default for name, (_, default, _) in PARAMETERS.items()}
    values.update(snapshot="at_spawns", map_file=str(SAMPLES_WORLD))
    values.update(changes)
    return load_feed(values, snapshots_dir=SNAPSHOTS)


def write(tmp_path, text: str) -> str:
    path = tmp_path / "snap.yaml"
    path.write_text(text)
    return str(path)


def test_a_snapshot_is_named_or_given_as_a_file():
    s = feed().snapshot
    assert (s.name, s.map, [r.robot_id for r in s.robots]) == ("at_spawns", "samples_world", ["robot_a", "robot_b"])
    assert load_snapshot(str(SNAPSHOTS / "b_on_edge.yaml"), SNAPSHOTS).robots[1].at == "corridor_1->pass_e"


def test_every_shipped_snapshot_is_one_the_gate_can_use():
    # The gate's own reading of each file; a snapshot it could not use would test nothing.
    sim_map = load_sim_map(SAMPLES_WORLD)
    assert snapshot_names(SNAPSHOTS) == ["at_spawns", "b_in_passage", "b_on_edge"]
    for name in snapshot_names(SNAPSHOTS):
        s = load_snapshot(name, SNAPSHOTS)
        starts = TeamStateStarts(sim_map, ("robot_a", "robot_b"), max_age=1.0)
        starts.observe(TeamSnapshot("", tuple(RobotState(r.robot_id, r.at, r.x, r.y, 10.0) for r in s.robots), 10.0))
        assert not isinstance(starts.at("ep1", now=10.0), UnknownStarts), name


def test_settings_have_their_defaults_and_are_checked():
    f = feed()
    assert (f.rate, f.stop_after, f.episode_id) == (2.0, 0.0, "")
    assert f.describe()["robots"]["robot_b"] == {"at": "east_n", "x": 12.0, "y": 1.2}
    for name, bad in (("rate", 0.0), ("rate", float("inf")), ("stop_after", -1.0)):
        with pytest.raises(ScenarioError, match=name):
            feed(**{name: bad})


def test_a_snapshot_for_another_map_is_refused():
    with pytest.raises(ScenarioError, match="written for map 'samples_world', not 'narrow_aisle'"):
        feed(map_file="", world="narrow_aisle")


@pytest.mark.parametrize("text, why", [
    ("map: samples_world\nrobots: []\n", "robots"),
    ("map: samples_world\nrobots:\n  - {robot_id: a, at: x, x: 0, y: 0, z: 1}\n", "Extra inputs"),
    ("map: samples_world\nrobots:\n  - {robot_id: a, at: x, x: 0, y: 0}\n  - {robot_id: a, at: y, x: 1, y: 0}\n",
     "two robots have the id 'a'"),
    ("robots:\n  - {robot_id: a, at: x, x: 0, y: 0}\n", "map"),
])
def test_a_snapshot_file_that_is_not_right_is_refused(tmp_path, text, why):
    with pytest.raises(ScenarioError, match=why):
        load_snapshot(write(tmp_path, text), SNAPSHOTS)


def test_no_snapshot_or_an_unknown_one_is_refused():
    with pytest.raises(ScenarioError, match="no snapshot given"):
        feed(snapshot="")
    with pytest.raises(ScenarioError, match="'nowhere' not found.*at_spawns, b_in_passage, b_on_edge"):
        feed(snapshot="nowhere")
