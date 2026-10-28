"""Reason codes. Pure Python."""
import re

import pytest

from mrs_common import reasons as R


def test_codes_are_lower_snake_case_and_unique():
    codes = [v for k, v in vars(R).items() if k.isupper() and isinstance(v, str)]
    assert len(codes) == len(set(codes)) == len(R.ALL)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]*", c) for c in codes)


def test_codes_shared_with_previous_study_keep_their_names():
    assert {"llm_timeout", "llm_malformed", "safety_override"} <= R.ALL


def test_check_and_detail():
    assert R.check(R.EDGE_BLOCKED) == "edge_blocked"
    assert R.is_reason("no_route") and not R.is_reason("teleported")
    assert R.detail(R.EDGE_BLOCKED, edge="corridor_1--room_3", robot="robot_a") == {
        "reason": "edge_blocked", "edge": "corridor_1--room_3", "robot": "robot_a"}
    with pytest.raises(ValueError):
        R.detail("robot_was_tired")


def test_planning_exhausted_matches_the_event_kind():
    # The same word is an EpisodeEvent kind (mrs_msgs) and the reason a fallback plan carries.
    assert R.PLANNING_EXHAUSTED == "planning_exhausted"
    assert R.EDGE_BLOCKED == "edge_blocked"
