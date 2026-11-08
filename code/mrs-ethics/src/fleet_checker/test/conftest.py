"""Test maps shared by several test files, and the check that every recheck of every test equals
a recheck done in full."""
import pytest

from fleet_checker.adapters.sim_map import sim_map_from_dict
from fleet_checker.adapters.team_check import TeamCheckResult


def assert_same_result(incremental: TeamCheckResult, full: TeamCheckResult) -> None:
    """Equal field by field, and built in the same order: the verdict reads plans, timings,
    origins and findings in the order they come in."""
    assert incremental == full
    for name in ("plans", "origins", "timings"):
        assert list(getattr(incremental.compiled, name)) == list(getattr(full.compiled, name)), name
    assert list(incremental.must_replan) == list(full.must_replan)


INCREMENTAL_RECHECK = TeamCheckResult.recheck   # the method itself, before shadow_rechecks wraps it


@pytest.fixture(autouse=True)
def shadow_rechecks(monkeypatch):
    """Every recheck in every test is also done in full (recheck_full), and the two results must be
    the same: the incremental recheck may only save work, never change an answer. The count of
    rechecks compared is returned, so a test can see that it ran."""
    incremental = TeamCheckResult.recheck
    compared = {"rechecks": 0}

    def recheck(self, team_plan):
        result = incremental(self, team_plan)
        assert_same_result(result, TeamCheckResult.recheck_full(self, team_plan))
        compared["rechecks"] += 1
        return result

    monkeypatch.setattr(TeamCheckResult, "recheck", recheck)
    return compared

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


# One open hall with a crossing at c: w -> c -> e and n -> c -> s cross there. A room has no
# capacity limit, so S-1 never stops two robots at c; only S-2 does, when a person stands there.
CROSSROADS = {
    "world": "crossroads",
    "nodes": {"w": {"x": 0.0, "y": 0.0}, "c": {"x": 5.0, "y": 0.0}, "e": {"x": 10.0, "y": 0.0},
              "n": {"x": 5.0, "y": 4.0}, "s": {"x": 5.0, "y": -4.0}},
    "edges": [["w", "c"], ["c", "e"], ["n", "c"], ["c", "s"]],
    "zones": [{"id": "hall", "type": "room", "rect": {"x_min": -1, "x_max": 11, "y_min": -5, "y_max": 5}}],
    "spawns": [{"node": "w"}, {"node": "n"}],
}


@pytest.fixture(scope="session")
def crossroads():
    return sim_map_from_dict(CROSSROADS)


# An open hall around a node c that several routes end at: w - p - c - e - f - h, with n and s
# off c. p is 0.5 m from c, close enough for a handover at c to be too tight for the margin.
# A room has no capacity limit, so S-1 never stops robots meeting at c; S-4 stops two robots
# from standing there at once.
PARKING = {
    "world": "parking",
    "nodes": {"w": {"x": 0.0, "y": 0.0}, "p": {"x": 4.5, "y": 0.0}, "c": {"x": 5.0, "y": 0.0},
              "n": {"x": 5.0, "y": 4.0}, "s": {"x": 5.0, "y": -4.0}, "e": {"x": 10.0, "y": 0.0},
              "f": {"x": 10.0, "y": 4.0}, "h": {"x": 14.0, "y": 4.0}},
    "edges": [["w", "p"], ["p", "c"], ["n", "c"], ["c", "s"], ["c", "e"], ["e", "f"], ["f", "h"]],
    "zones": [{"id": "hall", "type": "room", "rect": {"x_min": -1, "x_max": 15, "y_min": -5, "y_max": 5}}],
    "spawns": [{"node": "w"}, {"node": "n"}, {"node": "f"}],
}


@pytest.fixture(scope="session")
def parking():
    return sim_map_from_dict(PARKING)
