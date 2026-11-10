"""The timing benchmark: percentiles, the instruments, and one cell's samples."""
import pytest
import yaml

import fleet_checker.adapters.team_check as team_check
from bench import config
from bench.maps import load_maps
from bench.perf import Instruments, check_kwargs, measure, overhead, run_cell
from bench.scenarios import generate
from bench.stats import percentile, wilson

from fleet_checker.adapters.team_check import ALL_RULES, check_team_plan
from fleet_checker.adapters.verdict import to_verdict


@pytest.fixture(scope="module")
def maps():
    return load_maps()


def test_percentiles_are_nearest_rank_so_always_a_measured_value():
    values = list(range(100, 0, -1))          # 100 .. 1, unsorted on purpose
    assert [percentile(values, p) for p in (50, 95, 99, 100)] == [50, 95, 99, 100]
    assert percentile([7], 99) == 7
    assert percentile([1, 2, 3, 4], 50) == 2  # rank ceil(0.5 * 4) = 2, not the mean of 2 and 3
    with pytest.raises(ValueError):
        percentile([], 50)


def test_wilson_intervals():
    assert wilson(0, 10)[0] == 0.0 and wilson(10, 10)[1] == 1.0
    lo, hi = wilson(5, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.2366, 0.7634)


def test_the_gate_settings_are_the_shipped_config_but_for_the_speed():
    # The benchmark keeps the speed its reports were measured at, 0.5 m/s; gate.yaml now ships
    # 0.25 (Module 6's starting u_max). Every rule runs, with the fairness ledger at M = 3.
    shipped = yaml.safe_load((config.SRC / "mrs_ethical_gate/config/gate.yaml").read_text())
    p = shipped["mrs_ethical_gate"]["ros__parameters"]
    assert config.GATE["speed"] == 0.5
    assert {n: v for n, v in config.GATE.items() if n != "speed"} == {n: p[n] for n in config.GATE if n != "speed"}


def test_every_check_names_every_rule(maps, monkeypatch):
    import functools
    import bench.perf as perf
    seen = []
    monkeypatch.setattr(perf, "check_team_plan", functools.wraps(check_team_plan)(
        lambda *a, **kw: seen.append(kw) or check_team_plan(*a, **kw)))   # keeps the signature every_rule reads
    s = generate(maps, "narrow_aisle", 2, "high", 0)
    measure(s, maps["narrow_aisle"], Instruments(), rep=0)
    assert seen and all(kw["rules"] == ALL_RULES for kw in seen)


def first(maps, map_name, n, density, *, conflict):
    """The first scenario of a cell that has a conflict, or that has none."""
    for i in range(50):
        s = generate(maps, map_name, n, density, i)
        if bool(check_team_plan(s.plan, maps[map_name], s.starts, **check_kwargs(s)).findings) == conflict:
            return s
    raise AssertionError(f"no scenario with conflict={conflict}")


def conflicting(maps, map_name, n, density):
    return first(maps, map_name, n, density, conflict=True)


def test_the_instruments_time_the_outer_compile_and_every_recheck(maps):
    s = conflicting(maps, "bench_grid", 8, "high")
    original_compile, original_recheck = team_check.compile_team_plan, team_check.TeamCheckResult.recheck
    with Instruments() as inst:
        sample = measure(s, maps["bench_grid"], inst, rep=0)
        assert inst.compiles["check"] == 1                         # the check's own compile, once
        assert inst.rechecks >= 1 and inst.compiles["verdict"] == inst.rechecks   # one compile per recheck
    assert (team_check.compile_team_plan, team_check.TeamCheckResult.recheck) == (original_compile, original_recheck)
    assert sample.compile_ns + sample.detect_ns == sample.check_ns
    assert sample.check_ns + sample.verdict_ns == sample.total_ns
    assert 0 < sample.recheck_ns <= sample.verdict_ns and sample.rechecks == inst.rechecks
    assert sample.recheck_compile_ns <= sample.recheck_ns


def test_measuring_does_not_change_what_the_check_finds(maps):
    s = conflicting(maps, "samples_world", 4, "high")
    plain = to_verdict(check_team_plan(s.plan, maps["samples_world"], s.starts, **check_kwargs(s)))
    with Instruments() as inst:
        sample = measure(s, maps["samples_world"], inst, rep=0)
    assert (sample.verdict, sample.constraints) == (plain.verdict, len(plain.constraints))
    assert sample.findings > 0 and sample.findings == sample.s1 + sample.s2 + sample.s4


def test_an_approved_plan_needs_no_recheck(maps):
    s = first(maps, "bench_grid", 2, "low", conflict=False)
    with Instruments() as inst:
        sample = measure(s, maps["bench_grid"], inst, rep=0)
    assert (sample.verdict, sample.rechecks, sample.recheck_ns) == ("APPROVE", 0, 0)


def test_people_bring_s2_into_the_check(maps):
    s = generate(maps, "bench_grid", 8, "medium", 0, people=4)
    kwargs = check_kwargs(s)
    assert kwargs["people"] == s.people and kwargs["crowding"].radius == config.GATE["crowding_radius"]
    assert check_kwargs(generate(maps, "bench_grid", 8, "medium", 0))["people"] == ()


def test_a_cell_gives_every_scenario_once_per_repetition_after_its_warm_up(maps):
    scenarios = [generate(maps, "narrow_aisle", 2, "medium", i) for i in range(3)]
    with Instruments() as inst:
        samples = run_cell(maps, scenarios, repetitions=2, warmup=(1, 1), inst=inst)
    assert [(x.scenario_id, x.rep) for x in samples] == [(s.scenario_id, r) for r in range(2) for s in scenarios]


def test_the_overhead_check_pairs_plain_and_timed_runs_of_one_scenario(maps):
    scenarios = [generate(maps, "narrow_aisle", 2, "high", i) for i in range(2)]
    pairs = overhead(maps, scenarios, repetitions=2)
    assert [(p.scenario_id, p.rep) for p in pairs] == [(s.scenario_id, r) for r in range(2) for s in scenarios]
    assert all(p.plain_ns > 0 and p.timed_ns > 0 for p in pairs)
