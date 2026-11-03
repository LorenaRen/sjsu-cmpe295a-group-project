"""The example in examples/ that the fake planner produced still matches what the code does.

It goes into the demo, the poster and the report, so it must not go stale silently: if the
gate's verdicts change, this fails, and the example has to be captured again.
"""
import json
from pathlib import Path

from mrs_common.plan_schema import TeamPlan

from mrs_ethical_gate.dev_nodes.scenarios import load_scenario

PKG = Path(__file__).resolve().parents[1]
EXAMPLE = PKG / "examples" / "fake_planner_s1_round2.json"


def test_the_fake_planner_example_matches_the_loop_today(run_with_gate):
    example = json.loads(EXAMPLE.read_text())
    on = {topic: [m for m in example["messages"] if m["topic"] == topic]
          for topic in ("/mrs/proposed_plan", "/mrs/verdict", "/mrs/validated_plan")}
    first = TeamPlan.model_validate(on["/mrs/proposed_plan"][0]["plan"])
    assert first == load_scenario("s1_passage_no_order", PKG / "scenarios").plan
    # run_with_gate uses the gate settings of the run that made the example.
    assert {k: example["how_produced"]["gate_settings"][k] for k in ("speed", "margin")} == {"speed": 0.5,
                                                                                            "margin": 0.5}
    f, done = run_with_gate(first)
    assert done.outcome == example["fake_planner_summary"]["outcome"] == "approved"
    sent = [(v["verdict"], v["rule_ids"], v["rationale"], v["constraints"]) for v in on["/mrs/verdict"]]
    again = [(r["verdict"], r["rule_ids"], r["rationale"],
              [{k: v for k, v in c.items() if k not in ("result", "change", "why_not")} for c in r["constraints"]])
             for r in f.rounds]
    assert again == sent
    [validated] = on["/mrs/validated_plan"]
    assert validated["source_verdict"] == "APPROVE"
    assert TeamPlan.model_validate(validated["plan"]) == TeamPlan.model_validate(on["/mrs/proposed_plan"][-1]["plan"])
