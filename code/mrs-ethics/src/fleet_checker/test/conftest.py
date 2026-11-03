"""Test maps shared by several test files."""
import pytest

from fleet_checker.adapters.sim_map import sim_map_from_dict

# Two one-robot zones side by side between two rooms: robots crossing in
# opposite directions swap places head-on at x = 2.
TWO_NARROW_ZONES = {
    "world": "two_narrow_zones",
    "nodes": {"wr": {"x": 0.0, "y": 0.0}, "w": {"x": 1.5, "y": 0.0},
              "e": {"x": 2.5, "y": 0.0}, "er": {"x": 4.0, "y": 0.0}},
    "edges": [["wr", "w"], ["w", "e"], ["e", "er"]],
    "zones": [
        {"id": "west_room", "type": "room", "rect": {"x_min": -1, "x_max": 1, "y_min": -1, "y_max": 1}},
        {"id": "west", "type": "narrow", "capacity": 1,
         "rect": {"x_min": 1, "x_max": 2, "y_min": -0.4, "y_max": 0.4}},
        {"id": "east", "type": "narrow", "capacity": 1,
         "rect": {"x_min": 2, "x_max": 3, "y_min": -0.4, "y_max": 0.4}},
        {"id": "east_room", "type": "room", "rect": {"x_min": 3, "x_max": 5, "y_min": -1, "y_max": 1}},
    ],
    "spawns": [{"node": "wr"}, {"node": "er"}],
}


@pytest.fixture(scope="session")
def two_narrow_zones():
    return sim_map_from_dict(TWO_NARROW_ZONES)
