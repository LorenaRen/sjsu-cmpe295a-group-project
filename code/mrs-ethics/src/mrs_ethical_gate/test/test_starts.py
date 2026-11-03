"""Where each robot starts when a plan is checked."""
from pathlib import Path

import pytest

from fleet_checker.adapters.sim_map import load_sim_map
from mrs_ethical_gate.starts import node_from_at, spawn_start_nodes

NARROW_AISLE = Path(__file__).resolve().parents[2] / "mrs_sim/worlds/narrow_aisle.yaml"


@pytest.fixture(scope="module")
def aisle_map():
    return load_sim_map(NARROW_AISLE)


def test_robot_i_of_the_fleet_starts_at_spawn_slot_i(aisle_map):
    # The slots sim.launch.py uses: robot_a in the west room, robot_b in the east room.
    assert spawn_start_nodes(aisle_map, ("robot_a", "robot_b")) == {"robot_a": "room_w_s", "robot_b": "room_e_n"}


def test_more_robots_than_spawn_slots_is_refused(aisle_map):
    with pytest.raises(ValueError, match="4 spawn slots"):
        spawn_start_nodes(aisle_map, tuple(f"robot_{c}" for c in "abcde"))


def test_a_robot_at_a_node_starts_there():
    assert node_from_at("room_w") == "room_w"


def test_a_robot_on_an_edge_is_refused_for_now():
    with pytest.raises(ValueError, match=r"aisle_w->aisle_mid.*not supported yet"):
        node_from_at("aisle_w->aisle_mid")


def test_an_empty_at_is_refused():
    with pytest.raises(ValueError, match="empty"):
        node_from_at("")
