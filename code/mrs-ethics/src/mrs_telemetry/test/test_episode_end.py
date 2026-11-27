import pytest

from mrs_telemetry.episode_end import EndWatcher


def test_the_first_plan_after_the_mission_ends_the_episode_after_the_grace_period():
    w = EndWatcher(grace_s=5.0)
    assert not w.on_plan(1.0, "FALLBACK")        # before the mission: a leftover, does not count
    w.arm(2.0)
    assert not w.due(100.0)                      # replans may take long: no plan, no end
    assert w.on_plan(40.0, "APPROVE") and w.verdict == "APPROVE"
    assert not w.on_plan(41.0, "FALLBACK")       # only the first one counts
    assert not w.due(44.9) and w.due(45.0)


def test_grace():
    w = EndWatcher(grace_s=0)
    w.arm(0)
    w.on_plan(3.0, "FALLBACK")
    assert w.due(3.0)
    with pytest.raises(ValueError):
        EndWatcher(grace_s=-1)
