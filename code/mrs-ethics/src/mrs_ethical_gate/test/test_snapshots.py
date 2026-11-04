"""fake_team_state's snapshot files and parameters (pure Python)."""
from pathlib import Path

import pytest

from mrs_ethical_gate.dev_nodes.scenarios import ScenarioError
from mrs_ethical_gate.dev_nodes.snapshots import PARAMETERS, load_feed, load_snapshot, snapshot_names
from mrs_ethical_gate.session import GateSession
from mrs_ethical_gate.starts import PersonSeen, RobotState, TeamSnapshot, TeamStateStarts, UnknownStarts

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


def test_every_shipped_snapshot_reads_as_meant():
    # The gate's own reading of each file, as fake_team_state publishes it: a snapshot that did not
    # read as meant would test nothing. Only b_on_blocked_edge is meant to leave a robot unknown.
    sim_map = load_sim_map(SAMPLES_WORLD)
    assert snapshot_names(SNAPSHOTS) == ["at_spawns", "b_in_passage", "b_on_blocked_edge", "b_on_edge",
                                         "passage_blocked", "person_by_corridor_2"]
    for name in snapshot_names(SNAPSHOTS):
        s = load_snapshot(name, SNAPSHOTS)
        robots = tuple(RobotState(r.robot_id, r.at, r.x, r.y, 10.0,
                                  people=tuple(PersonSeen(p.person_id, p.x, p.y) for p in s.people
                                               if r.robot_id in p.seen_by))
                       for r in s.robots)
        starts = TeamStateStarts(sim_map, ("robot_a", "robot_b"), max_age=1.0)
        starts.observe(TeamSnapshot("", robots, 10.0, s.blocked_edges))
        got = starts.at("ep1", now=10.0)
        if name == "b_on_blocked_edge":
            assert [robot for robot, _ in got.problems] == ["robot_b"]
        else:
            assert not isinstance(got, UnknownStarts), name
            assert not got.blocked_not_on_map, name


def test_people_are_seen_by_every_robot_unless_the_file_says_who(tmp_path):
    text = ("map: samples_world\nrobots:\n  - {robot_id: robot_a, at: west_s, x: 0, y: -1.2}\n"
            "  - {robot_id: robot_b, at: east_n, x: 12, y: 1.2}\n"
            "people:\n  - {id: person_1, x: 10, y: -1}\n  - {id: person_2, x: 1, y: 1, seen_by: [robot_a]}\n")
    s = load_snapshot(write(tmp_path, text), SNAPSHOTS)
    assert [(p.person_id, p.x, p.y, p.seen_by) for p in s.people] == [
        ("person_1", 10.0, -1.0, ("robot_a", "robot_b")), ("person_2", 1.0, 1.0, ("robot_a",))]
    assert load_feed({**{n: d for n, (_, d, _) in PARAMETERS.items()}, "snapshot": write(tmp_path, text),
                      "map_file": str(SAMPLES_WORLD)}, snapshots_dir=SNAPSHOTS).describe()["people"] == {
        "person_1": {"x": 10.0, "y": -1.0, "seen_by": ["robot_a", "robot_b"]},
        "person_2": {"x": 1.0, "y": 1.0, "seen_by": ["robot_a"]}}


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
    ("map: samples_world\nrobots:\n  - {robot_id: a, at: x, x: 0, y: 0}\n"
     "people:\n  - {id: p, x: 0, y: 0, seen_by: [b]}\n", "p is seen by b, which is not a robot of this snapshot"),
    ("map: samples_world\nrobots:\n  - {robot_id: a, at: x, x: 0, y: 0}\n"
     "people:\n  - {id: p, x: 0, y: 0}\n  - {id: p, x: 1, y: 0}\n", "two people have the id 'p'"),
])
def test_a_snapshot_file_that_is_not_right_is_refused(tmp_path, text, why):
    with pytest.raises(ScenarioError, match=why):
        load_snapshot(write(tmp_path, text), SNAPSHOTS)


def test_no_snapshot_or_an_unknown_one_is_refused():
    with pytest.raises(ScenarioError, match="no snapshot given"):
        feed(snapshot="")
    with pytest.raises(ScenarioError, match="'nowhere' not found.*at_spawns, b_in_passage, b_on_blocked_edge"):
        feed(snapshot="nowhere")
