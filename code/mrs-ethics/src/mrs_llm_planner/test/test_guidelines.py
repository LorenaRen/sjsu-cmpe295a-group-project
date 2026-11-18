import pytest

from mrs_llm_planner.guidelines import BASIC_RULES, RULE_IDS, guidelines_for, load_guidelines, parse_rules


def test_every_gate_rule_has_a_guideline():
    from fleet_checker.adapters.team_check import ALL_RULES
    assert set(ALL_RULES.enabled()) == set(RULE_IDS)
    assert set(load_guidelines()["rules"]) == set(RULE_IDS)


def test_basic_rules_match_gate_defaults():
    from mrs_ethical_gate.settings import PARAMETERS
    on = {f"{k[5].upper()}-{k[6]}" for k, (_, d, _) in PARAMETERS.items() if k.startswith("rule_") and d}
    assert on == set(BASIC_RULES)


def test_guidelines_for_order_and_count():
    always = len(load_guidelines()["always"])
    texts = guidelines_for(["P-1", "S-1"])
    assert len(texts) == always + 2
    assert texts[always] == load_guidelines()["rules"]["S-1"]      # RULE_IDS order, not the caller's
    assert guidelines_for([]) == list(load_guidelines()["always"].values())


def test_unknown_rules():
    with pytest.raises(ValueError):
        guidelines_for(["X-9"])
    with pytest.raises(ValueError):
        parse_rules("S-1, X-9")
    assert parse_rules(" S-1,P-1 ,, P-2") == ("S-1", "P-1", "P-2")
