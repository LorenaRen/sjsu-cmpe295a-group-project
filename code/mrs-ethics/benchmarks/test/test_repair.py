"""The repair benchmark: the fake planner against the gate's session, and what the runs add up to."""
import pytest

from bench import config
from bench.maps import load_maps
from bench.repair import OUTCOMES, case_counts, classify, run_scenario, tally
from bench.scenarios import Scenario, generate

from mrs_ethical_gate.dev_nodes.scenarios import load_scenario

FAKE_PLANNER_SCENARIOS = config.SRC / "mrs_ethical_gate/scenarios"


@pytest.fixture(scope="module")
def maps():
    return load_maps()


def from_fake_planner(name: str) -> Scenario:
    """One of the fake planner's own scenarios, from samples_world's spawn slots."""
    plan = load_scenario(name, FAKE_PLANNER_SCENARIOS).plan
    return Scenario("samples_world", 2, "-", 0, name, {"robot_a": "west_s", "robot_b": "east_n"}, plan, 0)


@pytest.mark.parametrize("name, outcome, rounds", [
    ("s1_passage_no_order", "kept_targets", 2),        # an order_before, nothing moved
    ("s4_both_hold_room_3", "changed_targets", 2),     # robot_b, there first, ends somewhere else
    ("s4_end_where_b_stands", "stuck", 1),             # only a custom constraint
    ("p1_keep_clear_stop", "changed_targets", 4),      # robot_b's target moved three times
])
def test_the_fake_planners_own_scenarios_end_as_they_do_on_real_topics(maps, name, outcome, rounds):
    run = run_scenario(from_fake_planner(name), maps["samples_world"], max_attempts=5)
    assert (run.outcome, run.rounds) == (outcome, rounds)
    assert run.passed_in_round == (rounds if outcome != "stuck" else None)


def summary(outcome, rounds, targets=None):
    return {"outcome": outcome, "rounds": rounds, "passed_in_round": rounds if outcome == "approved" else None,
            "targets_changed": targets or {}}


def test_outcomes():
    assert classify(summary("approved", 1)) == "first_round"
    assert classify(summary("approved", 3)) == "kept_targets"
    assert classify(summary("approved", 2, {"t01": ["room_3", "corridor_2"]})) == "changed_targets"
    assert (classify(summary("stuck", 2)), classify(summary("exhausted", 5))) == ("stuck", "exhausted")
    with pytest.raises(ValueError):
        classify(summary("no_verdict", 1))
    assert OUTCOMES == ("first_round", "kept_targets", "changed_targets", "stuck", "exhausted")


def test_a_run_records_the_first_round_and_what_each_constraint_did(maps):
    runs = [run_scenario(generate(maps, "bench_grid", 8, "high", i), maps["bench_grid"], max_attempts=5)
            for i in range(4)]
    for run in runs:
        assert run.outcome in OUTCOMES and 1 <= run.rounds <= 5
        assert (run.first_findings == 0) == (run.outcome == "first_round")
        for c in run.constraints:
            assert c["received"] == c["changed"] + c["noted"] + c["not_applied"]
    assert any(run.constraints for run in runs)


def test_tally_counts_outcomes_with_intervals_and_the_constraints_by_kind(maps):
    runs = [run_scenario(generate(maps, "narrow_aisle", 2, d, i), maps["narrow_aisle"], max_attempts=5)
            for d in ("low", "high") for i in range(5)]
    t = tally(runs)
    assert t["scenarios"] == 10 and sum(t["outcomes"][o]["count"] for o in OUTCOMES) == 10
    conflicted = [r for r in runs if r.first_findings]
    assert t["with_conflict"]["scenarios"] == len(conflicted)
    for o in OUTCOMES:
        lo, hi = t["outcomes"][o]["interval"]
        assert 0.0 <= lo <= t["outcomes"][o]["share"] <= hi <= 1.0
    assert sum(t["passed_in_round"].values()) == sum(1 for r in runs if r.passed_in_round)
    kinds = t["by_kind"]
    assert all(k["received"] == k["changed"] + k["noted"] + k["not_applied"] for k in kinds.values())
    assert sum(c["received"] for c in t["by_case"].values()) == sum(k["received"] for k in kinds.values())


def test_a_run_counts_what_each_situation_led_to(maps):
    # VerdictContent.cases says which situation each constraint answers; the counts by situation
    # add up to the fake planner's own counts by kind and rule.
    runs = [run_scenario(generate(maps, "bench_grid", 8, "high", i), maps["bench_grid"], max_attempts=5)
            for i in range(4)]
    for run in runs:
        summed = {}
        for c in run.cases:
            assert c["case"] and c["received"] == c["changed"] + c["noted"] + c["not_applied"]
            summed[c["kind"], c["rule_id"]] = summed.get((c["kind"], c["rule_id"]), 0) + c["received"]
        assert summed == {(c["kind"], c["rule_id"]): c["received"] for c in run.constraints}
    assert {"yielder_waits"} <= {c["case"] for run in runs for c in run.cases if c["rule_id"] == "node_occupied"}


def test_situations_are_paired_with_what_the_fake_planner_did_round_by_round():
    rounds = [{"verdict": "REJECT", "constraints": [{"kind": "order_before", "rule_id": "node_occupied",
                                                     "result": "changed"},
                                                    {"kind": "custom", "rule_id": "node_occupied",
                                                     "result": "not_applied"}]},
              {"verdict": "APPROVE", "constraints": []}]
    assert case_counts(rounds, [("yielder_waits", "wait_nearby"), ()]) == (
        {"kind": "custom", "rule_id": "node_occupied", "case": "wait_nearby", "received": 1, "changed": 0,
         "noted": 0, "not_applied": 1},
        {"kind": "order_before", "rule_id": "node_occupied", "case": "yielder_waits", "received": 1,
         "changed": 1, "noted": 0, "not_applied": 0})
    # a checker from before VerdictContent.cases (compare.py runs old ones too): no situation
    assert {c["case"] for c in case_counts(rounds, [None, None])} == {"-"}
