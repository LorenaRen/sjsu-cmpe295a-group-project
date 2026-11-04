"""Where each robot starts when a plan is checked: the spawn slots, or Module 3's /mrs/team_state."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_checker.adapters.compiler import OnEdge
from fleet_checker.adapters.sim_map import load_sim_map
from mrs_ethical_gate.starts import (FixedStarts, RobotState, Starts, TeamSnapshot, TeamStateStarts,
                                     UnknownStarts, spawn_start_nodes, start_from, team_snapshot)

SRC = Path(__file__).resolve().parents[2]
NARROW_AISLE = SRC / "mrs_sim/worlds/narrow_aisle.yaml"
SAMPLES_WORLD = SRC / "fleet_checker/test/fixtures/samples_world.yaml"
FLEET = ("robot_a", "robot_b")
MAX_AGE = 1.0   # config/gate.yaml's placeholder


@pytest.fixture(scope="module")
def aisle_map():
    return load_sim_map(NARROW_AISLE)


@pytest.fixture(scope="module")
def samples_map():
    return load_sim_map(SAMPLES_WORLD)


# ---------------- spawns ----------------

def test_robot_i_of_the_fleet_starts_at_spawn_slot_i(aisle_map):
    # The slots sim.launch.py uses: robot_a in the west room, robot_b in the east room.
    assert spawn_start_nodes(aisle_map, ("robot_a", "robot_b")) == {"robot_a": "room_w_s", "robot_b": "room_e_n"}


def test_more_robots_than_spawn_slots_is_refused(aisle_map):
    with pytest.raises(ValueError, match="4 spawn slots"):
        spawn_start_nodes(aisle_map, tuple(f"robot_{c}" for c in "abcde"))


def test_fixed_starts_are_the_same_for_every_plan():
    starts = FixedStarts({"robot_b": "east_n", "robot_a": "west_s"})
    assert starts.fleet == FLEET
    assert starts.at("ep1", now=0.0) == starts.at("ep2", now=99.0) == Starts({"robot_a": "west_s", "robot_b": "east_n"})
    assert starts.at("ep1", now=0.0).summary() == {"source": "spawns"}


# ---------------- one robot's WorldState -> where its plan starts ----------------

def robot(robot_id="robot_b", at="east_n", x=12.0, y=1.2, stamp=10.0) -> RobotState:
    return RobotState(robot_id, at, x, y, stamp)


def test_a_robot_at_a_node_starts_there(samples_map):
    assert start_from(samples_map, robot(at="room_3")) == ("room_3", None)


def test_a_robot_on_an_edge_starts_where_the_edge_ends_and_drives_there_first(samples_map):
    state = robot(at="corridor_1->pass_e", x=8.6, y=0.1)
    assert start_from(samples_map, state) == ("pass_e", OnEdge("corridor_1", "pass_e", (8.6, 0.1)))


@pytest.mark.parametrize("at, x, y, why", [
    ("", 0.0, 0.0, "at is empty"),
    ("room_9", 0.0, 0.0, "at 'room_9' is not a node of map 'samples_world'"),
    ("corridor_1->room_2", 5.0, 0.0, "at 'corridor_1->room_2' is not an edge of map 'samples_world'"),
    ("corridor_1->", 9.0, 0.0, "at 'corridor_1->' is not a node or an edge"),
    ("corridor_1->pass_e", 8.6, 30.0, r"on corridor_1->pass_e, but its pose \(8.6, 30\) is outside every zone"),
])
def test_a_position_the_map_does_not_have_is_refused(samples_map, at, x, y, why):
    with pytest.raises(ValueError, match=why):
        start_from(samples_map, robot(at=at, x=x, y=y))


# ---------------- a TeamState message -> TeamSnapshot ----------------

def stamp(t: float) -> SimpleNamespace:
    return SimpleNamespace(sec=int(t), nanosec=round((t - int(t)) * 1e9))


def test_a_team_state_message_becomes_a_snapshot():
    msg = SimpleNamespace(
        episode_id="ep1", blocked_edges=["corridor_1--room_3"], stamp=stamp(10.25),
        robots=[SimpleNamespace(robot_id="robot_a", at="west_s", x=0.0, y=-1.2, stamp=stamp(10.5)),
                SimpleNamespace(robot_id="robot_b", at="corridor_1->pass_e", x=8.6, y=0.0, stamp=stamp(0.0))])
    assert team_snapshot(msg) == TeamSnapshot(
        episode_id="ep1", stamp=10.25, blocked_edges=("corridor_1--room_3",),
        robots=(RobotState("robot_a", "west_s", 0.0, -1.2, 10.5),
                RobotState("robot_b", "corridor_1->pass_e", 8.6, 0.0, None)))   # 0 s 0 ns: not set


# ---------------- the latest snapshot, read when a plan arrives ----------------

def snapshot(*robots: RobotState, episode_id="ep1", stamp=10.0, blocked_edges=()) -> TeamSnapshot:
    return TeamSnapshot(episode_id, tuple(robots), stamp, tuple(blocked_edges))


AT_SPAWNS = (robot("robot_a", "west_s", 0.0, -1.2), robot("robot_b", "east_n", 12.0, 1.2))


def team_state(samples_map, *snapshots) -> TeamStateStarts:
    starts = TeamStateStarts(samples_map, FLEET, max_age=MAX_AGE)
    for s in snapshots:
        starts.observe(s)
    return starts


def test_nothing_is_known_before_the_first_snapshot(samples_map):
    starts = team_state(samples_map)
    assert starts.fleet == FLEET
    assert starts.at("ep1", now=10.0) == UnknownStarts(((None, "no /mrs/team_state has been received yet"),))


def test_the_latest_snapshot_gives_every_robot_its_start(samples_map):
    on_edge = robot("robot_b", "corridor_1->pass_e", 8.6, 0.0, stamp=10.25)
    starts = team_state(samples_map, snapshot(*AT_SPAWNS), snapshot(AT_SPAWNS[0], on_edge, stamp=10.5)).at("ep1", now=10.75)
    assert starts.start_nodes == {"robot_a": "west_s", "robot_b": "pass_e"}
    assert starts.on_edges == {"robot_b": OnEdge("corridor_1", "pass_e", (8.6, 0.0))}
    assert (starts.snapshot_age, starts.ages) == (0.25, {"robot_a": 0.75, "robot_b": 0.5})
    assert starts.summary() == {
        "source": "team_state", "snapshot_age_s": 0.25, "left_out": [], "blocked_edges_not_used": [],
        "robots": {"robot_a": {"at": "west_s", "age_s": 0.75}, "robot_b": {"at": "corridor_1->pass_e", "age_s": 0.5}}}


def test_a_robot_without_a_stamp_of_its_own_is_as_old_as_the_snapshot(samples_map):
    a, b = AT_SPAWNS
    starts = team_state(samples_map, snapshot(a._replace(stamp=None), b, stamp=9.5)).at("ep1", now=10.0)
    assert starts.ages == {"robot_a": 0.5, "robot_b": 0.0}
    unstamped = team_state(samples_map, snapshot(a._replace(stamp=None), b, stamp=None)).at("ep1", now=10.0)
    assert unstamped.problems == (("robot_a", "its position has no time stamp"),)


def test_a_position_older_than_the_limit_is_unknown(samples_map):
    a, b = AT_SPAWNS
    starts = team_state(samples_map, snapshot(a, b._replace(stamp=8.5)))
    assert isinstance(starts.at("ep1", now=9.5), Starts)            # exactly 1 s old
    assert starts.at("ep1", now=10.0).problems == (
        ("robot_b", "its position is 1.5 s old, more than team_state_max_age (1 s)"),)


def test_a_position_stamped_ahead_of_the_gates_clock_is_unknown(samples_map):
    starts = team_state(samples_map, snapshot(*AT_SPAWNS))           # stamped 10 s
    assert isinstance(starts.at("ep1", now=9.2), Starts)             # 0.8 s ahead: clock jitter
    assert starts.at("ep1", now=0.0).problems == tuple(
        (r, "its position is stamped 10.0 s ahead of the gate's clock; do the gate and Module 3 use "
            "the same clock (use_sim_time)?") for r in FLEET)


def test_a_robot_missing_from_the_snapshot_is_unknown(samples_map):
    starts = team_state(samples_map, snapshot(AT_SPAWNS[0])).at("ep1", now=10.0)
    assert starts.problems == (("robot_b", "it is not in the latest /mrs/team_state"),)


def test_a_position_the_map_does_not_have_is_unknown(samples_map):
    starts = team_state(samples_map, snapshot(AT_SPAWNS[0], robot(at="room_9"))).at("ep1", now=10.0)
    assert starts.problems == (("robot_b", "at 'room_9' is not a node of map 'samples_world'"),)


def test_a_snapshot_of_another_episode_says_nothing(samples_map):
    starts = team_state(samples_map, snapshot(*AT_SPAWNS, episode_id="ep0"))
    assert starts.at("ep1", now=10.0).problems == (
        (None, "the latest /mrs/team_state is of episode 'ep0', not 'ep1'"),)
    # A snapshot that names no episode goes for any.
    assert isinstance(team_state(samples_map, snapshot(*AT_SPAWNS, episode_id="")).at("ep1", now=10.0), Starts)


def test_robots_outside_the_fleet_are_left_out_and_logged(samples_map):
    extra = robot("robot_c", "room_3", 12.0, 0.0)
    starts = team_state(samples_map, snapshot(*AT_SPAWNS, extra, blocked_edges=["corridor_1--room_3"])).at("ep1", now=10.0)
    assert set(starts.start_nodes) == set(FLEET)
    summary = starts.summary()
    assert (summary["left_out"], summary["blocked_edges_not_used"]) == (["robot_c"], ["corridor_1--room_3"])


def test_unknown_positions_are_logged_with_the_reasons(samples_map):
    starts = team_state(samples_map, snapshot(AT_SPAWNS[0], stamp=9.0)).at("ep1", now=10.0)
    assert starts.summary() == {"source": "team_state", "snapshot_age_s": 1.0,
                                "unknown": [{"robot": "robot_b", "why": "it is not in the latest /mrs/team_state"}]}
    assert team_state(samples_map).at("ep1", now=10.0).summary() == {
        "source": "team_state", "snapshot_age_s": None,
        "unknown": [{"robot": None, "why": "no /mrs/team_state has been received yet"}]}


def test_the_age_limit_must_be_a_time(samples_map):
    for bad in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError, match="max_age"):
            TeamStateStarts(samples_map, FLEET, max_age=bad)
