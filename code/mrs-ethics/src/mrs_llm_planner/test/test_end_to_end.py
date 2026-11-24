"""Agents (fake LLM) + coordinator protocol + the real gate (GateSession), on narrow_aisle.

robot_a starts in the west room and must reach room_e; robot_b the other way round (room_w).
Each agent proposes only its own part; the coordinator merges them into the team plan.
Taking turns through the one-lane aisle is approved. Both robots in the aisle at once is
rejected (S-1), and every robot holds: there is one dialogue per mission, no replanning.
"""
import pytest

from mrs_common.plan_schema import constraints_to_json, plan_to_json
from mrs_llm_planner.agent import Agent, TranscriptTracker, failure_text
from mrs_llm_planner.guidelines import BASIC_RULES, guidelines_for
from mrs_llm_planner.llm_client import FakeClient
from mrs_llm_planner.prompts import PlannerContext
from mrs_llm_planner.protocol import (CycleDone, Coordinator, Fallback, ProtocolConfig, SendPrompt, StartReplan,
                                      SubmitPlan, Turn, Verdict)
from plans import A_GOES, B_AFTER_A, B_NOW, HEAD_ON, TAKE_TURNS, agreement, discussion, part, proposal

TEAM = ("robot_a", "robot_b")


@pytest.fixture
def gate():
    from fleet_checker.adapters.sim_map import load_sim_map
    from fleet_checker.adapters.team_check import Rules
    from mrs_ethical_gate.session import GateSession
    from mrs_ethical_gate.starts import spawn_start_nodes
    from mrs_sim.world_map import resolve_world

    sim_map = load_sim_map(resolve_world("narrow_aisle")[1])
    basic = Rules(s1=True, s2=False, s4=False, p1=True, p2=True, p3=False)
    return GateSession(sim_map, spawn_start_nodes(sim_map, TEAM), speed=0.25, robot_length=0.45,
                       rules=basic, fairness_ledger=False)


def run(world_map, gate, scripts, mission="robot_a: go to room_e. robot_b: go to room_w.", max_replan_attempts=3,
        replan_responses=None):
    """Drive the protocol the way the nodes do; returns (actions, verdicts, agents' fakes).

    replan_responses: optional list of (success, plan) tuples for mock replan results.
                      If None and replan is triggered, falls back immediately.
    """
    fakes = {r: FakeClient(script=scripts[r]) for r in TEAM}
    from mrs_llm_planner.routes import node_zones
    spawns = {r: s.node for r, s in zip(TEAM, world_map.spawns)}
    agents = {r: Agent(PlannerContext(r, TEAM, world_map.to_prompt_dict(), tuple(guidelines_for(BASIC_RULES)),
                                      node_zones=node_zones(world_map), start_nodes=spawns),
                       fakes[r]) for r in TEAM}
    trackers = {r: TranscriptTracker() for r in TEAM}
    coord = Coordinator(ProtocolConfig(team=TEAM, max_replan_attempts=max_replan_attempts), new_id=lambda: "tx-e2e",
                        start_nodes={r: s.node for r, s in zip(TEAM, world_map.spawns)})

    log, verdicts, now = [], [], 0.0
    replan_idx = 0
    from mrs_llm_planner.mission import parse_mission, room_nodes
    pending = coord.start_cycle(parse_mission(mission, TEAM, world_map.nodes, room_nodes(world_map)), "ep-1", now)
    while pending:
        action = pending.pop(0)
        log.append(action)
        now += 1.0
        if isinstance(action, SendPrompt):
            for t in trackers.values():   # every agent hears every turn
                t.hear(transaction_id=action.transaction_id, round=action.round, kind="prompt",
                       speaker_id="coordinator", text=action.text)
            r = action.next_speaker
            result = agents[r].take_turn(trackers[r].request(round=action.round))
            if not result.ok:   # as agent_node: every failure is said out loud and skips the turn
                kind, text, plan = "discussion", failure_text(result), None
            else:
                kind, text, plan = result.reply.kind, result.reply.text, result.reply.plan
            for t in trackers.values():
                t.hear(transaction_id=action.transaction_id, round=action.round, kind=kind, speaker_id=r,
                       text=text, plan_json=plan_to_json(plan) if plan else "")
            pending += coord.on_turn(Turn(action.transaction_id, action.round, kind, r, text, plan), now)
        elif isinstance(action, SubmitPlan):
            d = gate.decide(plan_to_json(action.plan), transaction_id=action.transaction_id, episode_id="ep-1",
                            attempt=action.attempt, now=now)
            verdicts.append(d.verdict)
            pending += coord.on_verdict(Verdict(action.transaction_id, d.verdict.verdict, d.verdict.rule_ids,
                                                d.verdict.rationale, constraints_to_json(d.verdict.constraints),
                                                action.attempt), now)
        elif isinstance(action, StartReplan):
            # Simulate replan result
            if replan_responses and replan_idx < len(replan_responses):
                success, plan = replan_responses[replan_idx]
                replan_idx += 1
                pending += coord.on_replan_result(success=success, plan=plan,
                                                  error_reason="" if success else "replan_failed",
                                                  error_detail="" if success else "mock failure", now=now)
            else:
                # No mock response: fail the replan
                pending += coord.on_replan_result(success=False, plan=None,
                                                  error_reason="replan_failed",
                                                  error_detail="no mock response", now=now)
    return log, verdicts, fakes


def test_taking_turns_is_approved(world_map, gate):
    scripts = {"robot_a": [proposal(part(A_GOES), "I cross first")],          # repeats: unchanged = accept
               "robot_b": [proposal(part(B_AFTER_A), "I follow after a1")]}
    log, verdicts, fakes = run(world_map, gate, scripts)
    assert [v.verdict for v in verdicts] == ["APPROVE"]
    assert log[-1] == CycleDone("tx-e2e", "approved")
    seen = fakes["robot_b"].calls[0][-1].content     # robot_b saw the mission, goals and robot_a's part
    assert "Mission: robot_a: go to room_e" in seen and "robot_a PROPOSES ITS PART (team plan version 1)" in seen
    assert "Robots now (start positions): robot_a at room_w_s; robot_b at room_e_n." in seen
    assert sum(isinstance(a, SendPrompt) for a in log) == 3   # a, b, a


def test_head_on_is_rejected_and_robots_hold(world_map, gate):
    scripts = {"robot_a": [proposal(part(A_GOES))], "robot_b": [proposal(part(B_NOW), "I go now")]}
    log, verdicts, _ = run(world_map, gate, scripts, max_replan_attempts=0)
    assert [v.verdict for v in verdicts] == ["REJECT"]
    assert verdicts[0].rule_ids == ("capacity_exceeded",)   # S-1; the gate reports reason codes
    assert isinstance(log[-2], Fallback) and log[-2].cause == "rejected" and log[-1].outcome == "fallback"
    assert sum(isinstance(a, SubmitPlan) for a in log) == 1


def test_no_agreement_falls_back(world_map, gate):
    scripts = {"robot_a": [discussion("I go first")], "robot_b": [discussion("no, I go first")]}
    log, verdicts, _ = run(world_map, gate, scripts)
    assert verdicts == [] and isinstance(log[-2], Fallback) and log[-1].outcome == "fallback"
    assert sum(isinstance(a, SendPrompt) for a in log) == 3 * 2   # rounds x agents


def test_whole_team_plan_from_one_agent_only_sets_its_own_part(world_map, gate):
    """phi3.5 writes everyone's tasks; robot_b still has to propose its own part."""
    scripts = {"robot_a": [proposal(TAKE_TURNS)], "robot_b": [proposal(part(B_AFTER_A))]}
    log, verdicts, fakes = run(world_map, gate, scripts)
    assert "Parts still missing: robot_b." in fakes["robot_b"].calls[0][-1].content
    assert [v.verdict for v in verdicts] == ["APPROVE"]


def test_malformed_agent_says_so_and_the_dialogue_goes_on(world_map, gate):
    scripts = {"robot_a": ["not json", "still not json", proposal(part(A_GOES))],
               "robot_b": [discussion("waiting for robot_a"), proposal(part(B_AFTER_A))]}
    log, verdicts, fakes = run(world_map, gate, scripts)
    assert not any(isinstance(a, Fallback) for a in log)
    assert "robot_a: I skip this turn: I could not produce a valid plan." in fakes["robot_b"].calls[0][-1].content
    assert [v.verdict for v in verdicts] == ["APPROVE"]


def test_agent_repairs_a_part_that_misses_its_goal(world_map, gate):
    """robot_b first stops in the aisle, not in its goal room_w: its own repair fixes it privately."""
    stops = {"robot_id": "robot_b", "tasks": [dict(B_AFTER_A["tasks"][0], target="aisle_mid")]}
    scripts = {"robot_a": [proposal(part(A_GOES))],
               "robot_b": [proposal(part(stops), "I stop halfway"), proposal(part(B_AFTER_A), "fixed")]}
    log, verdicts, fakes = run(world_map, gate, scripts)
    repair = fakes["robot_b"].calls[1][-1].content
    assert "robot_b's last task ends at aisle_mid, but its mission goal is room_w (end at" in repair
    assert ("Goals: robot_a -> room_e (end at room_e, room_e_n or room_e_s); "
            "robot_b -> room_w (end at room_w, room_w_n or room_w_s).") in fakes["robot_a"].calls[0][-1].content
    assert "I stop halfway" not in fakes["robot_a"].calls[1][-1].content   # stayed private
    assert [v.verdict for v in verdicts] == ["APPROVE"] and log[-1].outcome == "approved"


def test_agent_that_keeps_missing_its_goal_skips_its_turn(world_map, gate):
    stops = {"robot_id": "robot_b", "tasks": [dict(B_AFTER_A["tasks"][0], target="aisle_mid")]}
    scripts = {"robot_a": [proposal(part(A_GOES))],
               "robot_b": [proposal(part(stops)), proposal(part(stops)), proposal(part(B_AFTER_A))]}
    log, verdicts, fakes = run(world_map, gate, scripts)
    seen_by_a = fakes["robot_a"].calls[1][-1].content
    assert "- robot_b: I skip this turn: I could not produce a valid plan. Problem: robot_b's last task" in seen_by_a
    assert not any(isinstance(a, Fallback) for a in log)
    assert [v.verdict for v in verdicts] == ["APPROVE"]


def test_llm_timeout_skips_the_turn_instead_of_waiting(world_map, gate):
    """Never silent: a timed-out agent says so and the next robot speaks."""
    from mrs_llm_planner.llm_client import LLMTimeout
    scripts = {"robot_a": [LLMTimeout("slow"), proposal(part(A_GOES))],
               "robot_b": [discussion("waiting for robot_a"), proposal(part(B_AFTER_A))]}
    log, verdicts, fakes = run(world_map, gate, scripts)
    assert "robot_a: I skip this turn: my language model did not answer in time." in fakes["robot_b"].calls[0][-1].content
    assert [v.verdict for v in verdicts] == ["APPROVE"]


def test_every_call_carries_this_turns_schema(world_map, gate):
    scripts = {"robot_a": [proposal(part(A_GOES))], "robot_b": [proposal(part(B_AFTER_A))]}
    _, _, fakes = run(world_map, gate, scripts)
    first_b = fakes["robot_b"].schemas[0]
    prop = first_b["anyOf"][0]["properties"]["plan"]["properties"]["assignments"]["items"]
    assert prop["properties"]["robot_id"] == {"enum": ["robot_b"]}
    dep = prop["properties"]["tasks"]["items"]["properties"]["depends_on"]["items"]
    assert {"enum": ["a1"]} in dep["anyOf"]                      # robot_a's current task ids
    assert fakes["robot_a"].schemas[0] is not None


# ---- Replan end-to-end tests ----

def test_reject_then_replan_succeeds(world_map, gate):
    """REJECT triggers replan; successful replan with valid plan is APPROVED."""
    from mrs_common.plan_schema import plan_from_json
    import json

    # First plan: head-on collision (both go at once) -> REJECT
    scripts = {"robot_a": [proposal(part(A_GOES))], "robot_b": [proposal(part(B_NOW), "I go now")]}

    # Replan returns a valid plan: robot_b waits for robot_a
    fixed_plan = plan_from_json(json.dumps(TAKE_TURNS))

    log, verdicts, _ = run(world_map, gate, scripts, max_replan_attempts=3,
                           replan_responses=[(True, fixed_plan)])

    assert [v.verdict for v in verdicts] == ["REJECT", "APPROVE"]
    assert log[-1] == CycleDone("tx-e2e", "approved")
    assert sum(isinstance(a, StartReplan) for a in log) == 1
    assert sum(isinstance(a, SubmitPlan) for a in log) == 2  # original + replan


def test_reject_replan_fails_then_fallback(world_map, gate):
    """REJECT triggers replan; failed replan leads to fallback."""
    scripts = {"robot_a": [proposal(part(A_GOES))], "robot_b": [proposal(part(B_NOW), "I go now")]}

    # Replan fails
    log, verdicts, _ = run(world_map, gate, scripts, max_replan_attempts=3,
                           replan_responses=[(False, None)])

    assert [v.verdict for v in verdicts] == ["REJECT"]
    assert isinstance(log[-2], Fallback) and log[-2].cause == "replan_failed"
    assert log[-1].outcome == "fallback"


def test_multiple_replan_attempts_until_success(world_map, gate):
    """Multiple replan attempts, eventually succeeds."""
    from mrs_common.plan_schema import plan_from_json
    import json

    scripts = {"robot_a": [proposal(part(A_GOES))], "robot_b": [proposal(part(B_NOW))]}
    fixed_plan = plan_from_json(json.dumps(TAKE_TURNS))

    # First replan returns bad plan (still collision), second returns good plan
    bad_plan = plan_from_json(json.dumps(HEAD_ON))  # still head-on

    log, verdicts, _ = run(world_map, gate, scripts, max_replan_attempts=3,
                           replan_responses=[(True, bad_plan), (True, fixed_plan)])

    # First REJECT, replan1 -> REJECT, replan2 -> APPROVE
    assert [v.verdict for v in verdicts] == ["REJECT", "REJECT", "APPROVE"]
    assert log[-1].outcome == "approved"
    assert sum(isinstance(a, StartReplan) for a in log) == 2


def test_replan_exhausted_falls_back(world_map, gate):
    """All replan attempts rejected, falls back."""
    from mrs_common.plan_schema import plan_from_json
    import json

    scripts = {"robot_a": [proposal(part(A_GOES))], "robot_b": [proposal(part(B_NOW))]}
    bad_plan = plan_from_json(json.dumps(HEAD_ON))

    # All replans return bad plans
    log, verdicts, _ = run(world_map, gate, scripts, max_replan_attempts=2,
                           replan_responses=[(True, bad_plan), (True, bad_plan)])

    # REJECT x3 (original + 2 replans), then fallback
    assert [v.verdict for v in verdicts] == ["REJECT", "REJECT", "REJECT"]
    assert isinstance(log[-2], Fallback)
    assert log[-1].outcome == "fallback"
