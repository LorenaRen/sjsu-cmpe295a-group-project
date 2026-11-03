"""Shared by the fake planner's tests: samples_world, and the loop against the gate's session."""
from pathlib import Path

import pytest
from mrs_common.plan_schema import TeamPlan, constraints_to_json, plan_to_json

from fleet_checker.adapters.sim_map import load_sim_map
from mrs_ethical_gate.dev_nodes.replan_loop import FakePlanner, GateVerdict, Proposal
from mrs_ethical_gate.session import GateSession
from mrs_ethical_gate.starts import spawn_start_nodes

SAMPLES_WORLD = Path(__file__).resolve().parents[2] / "fleet_checker/test/fixtures/samples_world.yaml"


@pytest.fixture(scope="session")
def samples_world():
    return load_sim_map(SAMPLES_WORLD)


@pytest.fixture
def run_with_gate(samples_world):
    """run(plan, max_attempts=5) -> (FakePlanner, how it finished): the fake planner and the
    gate's GateSession on samples_world, wired the way the two nodes are, minus ROS. Verdicts
    go through constraints_json, as on /mrs/verdict."""
    def run(plan: TeamPlan, max_attempts: int = 5):
        starts = spawn_start_nodes(samples_world, ["robot_a", "robot_b"])
        gate = GateSession(samples_world, starts, speed=0.5, margin=0.5)   # config/gate.yaml; nobody around
        f = FakePlanner(plan, graph=samples_world.neighbors, start_nodes=starts, max_attempts=max_attempts,
                        transaction_id="txn-1", episode_id="ep-1")
        step = f.first()
        while isinstance(step, Proposal):
            v = gate.decide(plan_to_json(step.plan), transaction_id="txn-1", episode_id="ep-1",
                            attempt=step.attempt, now=0.0).verdict
            step = f.on_verdict(GateVerdict(transaction_id="txn-1", attempt=step.attempt, verdict=v.verdict,
                                            rule_ids=v.rule_ids, risk_score=v.risk_score, rationale=v.rationale,
                                            constraints_json=constraints_to_json(v.constraints)))
        return f, step
    return run
