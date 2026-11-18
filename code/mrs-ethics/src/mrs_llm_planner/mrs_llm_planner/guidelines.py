"""The rules the agents are told, in plain language (Module 4, step 3). Pure Python.

  guidelines_for(["S-1", "P-1", "P-2"])   # -> [always..., S-1, P-1, P-2] texts, in that order

The texts live in guidelines.yaml, keyed by the gate's rule ids. An enabled rule without a
guideline is an error, not a silent gap: the agents must be told exactly the rules the gate
checks, or the "gate vs. prompting alone" comparison stops being fair.
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Iterable

import yaml

RULE_IDS = ("S-1", "S-2", "S-4", "P-1", "P-2", "P-3")
BASIC_RULES = ("S-1", "P-1", "P-2")   # the gate's defaults (mrs_ethical_gate settings)


@lru_cache(maxsize=1)
def load_guidelines() -> dict:
    text = resources.files(__package__).joinpath("guidelines.yaml").read_text()
    data = yaml.safe_load(text)
    if set(data) != {"always", "rules"}:
        raise ValueError("guidelines.yaml must have exactly the keys 'always' and 'rules'")
    unknown = set(data["rules"]) - set(RULE_IDS)
    if unknown:
        raise ValueError(f"guidelines.yaml names unknown rule ids {sorted(unknown)}; known: {RULE_IDS}")
    return data


def parse_rules(text: str) -> tuple[str, ...]:
    """'S-1, P-1,P-2' -> ('S-1', 'P-1', 'P-2'); every id must be a known rule."""
    rules = tuple(r.strip() for r in text.split(",") if r.strip())
    unknown = [r for r in rules if r not in RULE_IDS]
    if unknown:
        raise ValueError(f"unknown rule id(s) {unknown}; known: {', '.join(RULE_IDS)}")
    return rules


def guidelines_for(rules: Iterable[str]) -> list[str]:
    """The 'always' guidelines, then one per enabled rule, in RULE_IDS order."""
    data = load_guidelines()
    rules = set(rules)
    unknown = rules - set(RULE_IDS)
    if unknown:
        raise ValueError(f"unknown rule id(s) {sorted(unknown)}")
    missing = sorted(rules - set(data["rules"]))
    if missing:
        raise ValueError(f"no guideline for enabled rule(s) {missing}: add them to guidelines.yaml")
    return list(data["always"].values()) + [data["rules"][r] for r in RULE_IDS if r in rules]
