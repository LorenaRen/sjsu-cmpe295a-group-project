"""The coordinator's state machine, driven by hand. Each agent proposes its own part; the turns
carry the team plan with that part merged in (as agent.py publishes it)."""
import json

import pytest

from mrs_common import reasons as R
from mrs_common.plan_schema import plan_from_json
from mrs_llm_planner.mission import Goal, Mission
from mrs_llm_planner.protocol import (CycleDone, Coordinator, Fallback, PauseWorld, ProtocolConfig, ResumeWorld,
                                      StartReplan, Turn, Verdict, fallback_plan, hold_targets)
from plans import A_GOES, B_AFTER_A, B_NOW, HEAD_ON, TAKE_TURNS, part

TP = lambda d: plan_from_json(json.dumps(d))
A_ONLY = TP(part(A_GOES))                  # robot_a's first turn publishes only its part
TAKE = TP(TAKE_TURNS)                      # robot_b's part merged in: a1, then b1 after a1
HEAD = TP(HEAD_ON)                         # robot_b's part without the wait


def make(**kw):
    return Coordinator(ProtocolConfig(team=("robot_a", "robot_b"), **kw), new_id=lambda: "tx-1")


def turn(c, kind, plan=None, text=""):
    """The turn the coordinator is waiting for, from whoever it named."""
    return Turn(c.transaction_id, c.round, kind, c.next_speaker, text, plan)


def kinds(actions):
    return [type(a).__name__ for a in actions]


def agree_on_take_turns(c, now=1.0):
    """robot_a proposes its part, robot_b its part (team plan = TAKE_TURNS), robot_a accepts."""
    c.on_turn(turn(c, "proposal", A_ONLY), now)
    c.on_turn(turn(c, "proposal", TAKE), now)
    return c.on_turn(turn(c, "agreement"), now)


def test_parts_merge_and_agreement():
    c = make()
    out = c.start_cycle("swap rooms", "ep", now=0.0)
    assert kinds(out) == ["SendPrompt"] and out[0].next_speaker == "robot_a" and "Mission: swap rooms" in out[0].text
    out = c.on_turn(turn(c, "proposal", A_ONLY), now=1.0)
    assert kinds(out) == ["SendPrompt"] and out[0].next_speaker == "robot_b"
    assert c.view.change == 1 and c.accepted == ["robot_a"] and c.view.missing(c.config.team) == ["robot_b"]
    out = c.on_turn(turn(c, "proposal", TAKE), now=2.0)     # robot_b adds its part: the plan changes
    assert kinds(out) == ["SendPrompt"] and out[0].round == 2 and out[0].next_speaker == "robot_a"
    assert c.view.change == 2 and c.accepted == ["robot_b"]
    out = c.on_turn(turn(c, "agreement"), now=3.5)
    assert kinds(out) == ["SubmitPlan"]
    s = out[0]
    assert s.plan.assignments == TAKE.assignments and s.transaction_id == "tx-1" and s.latency_ms == 3500.0
    assert s.dialogue_digest == "robot_a proposal; robot_b proposal; robot_a agreement"
    out = c.on_verdict(Verdict("tx-1", "APPROVE"), now=4.0)
    assert out == [CycleDone("tx-1", "approved")] and c.state == "done"


def test_only_the_speakers_own_part_counts():
    """robot_a's turn carries a whole team plan: robot_b's assignment in it is ignored."""
    c = make()
    c.start_cycle("m", "ep", 0)
    c.on_turn(turn(c, "proposal", TAKE), 1)
    assert list(c.view.parts) == ["robot_a"] and c.view.missing(c.config.team) == ["robot_b"]


def test_unchanged_part_counts_as_accepting():
    c = make()
    c.start_cycle("m", "ep", 0)
    c.on_turn(turn(c, "proposal", A_ONLY), 1)
    c.on_turn(turn(c, "proposal", TAKE), 2)
    reworded = json.loads(json.dumps(TAKE_TURNS))
    reworded["assignments"][0]["tasks"][0]["justification"] = "other words"
    out = c.on_turn(turn(c, "proposal", TP(reworded)), 3)   # robot_a: same part, new wording
    assert kinds(out) == ["SubmitPlan"]


def test_changed_part_needs_everyone_again():
    c = make()
    c.start_cycle("m", "ep", 0)
    c.on_turn(turn(c, "proposal", A_ONLY), 1)
    c.on_turn(turn(c, "proposal", HEAD), 2)                # robot_b: no wait
    c.on_turn(turn(c, "discussion", text="robot_b, please wait for a1"), 3)
    out = c.on_turn(turn(c, "proposal", TAKE), 4)           # robot_b changes its part
    assert kinds(out) == ["SendPrompt"] and c.view.change == 3 and c.accepted == ["robot_b"]
    assert kinds(c.on_turn(turn(c, "agreement"), 5)) == ["SubmitPlan"]


def test_agreement_before_any_part_and_with_parts_missing():
    c = make()
    c.start_cycle("m", "ep", 0)
    c.on_turn(turn(c, "agreement"), 1)                     # nothing to accept yet
    assert c.view.change == 0
    c.on_turn(turn(c, "proposal", TP(part(B_NOW))), 2)       # robot_b's part only
    out = c.on_turn(turn(c, "agreement"), 3)                # robot_a accepts, but has no part
    assert kinds(out) == ["SendPrompt"] and c.view.missing(c.config.team) == ["robot_a"]


def test_no_agreement_falls_back():
    c = make(max_rounds=2)
    c.start_cycle("m", "ep", 0)
    for _ in range(3):
        assert kinds(c.on_turn(turn(c, "discussion"), 1)) == ["SendPrompt"]
    out = c.on_turn(turn(c, "discussion"), 1)
    assert kinds(out) == ["Fallback", "CycleDone"]
    assert out[0].cause == R.NO_AGREEMENT and out[1].outcome == "fallback" and c.state == "done"


def test_reject_triggers_replan():
    """REJECT with max_replan_attempts > 0 triggers replan instead of fallback."""
    c = make(max_replan_attempts=3)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)
    out = c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "robots meet in the aisle",
                               constraints_json='[{"kind": "order_before"}]'), 2)
    assert kinds(out) == ["StartReplan"]
    assert out[0].attempt == 1
    assert c.state == "replanning"


def test_reject_falls_back_when_no_replan():
    """REJECT with max_replan_attempts=0 falls back immediately."""
    c = make(max_replan_attempts=0)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)
    out = c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "robots meet in the aisle"), 2)
    assert kinds(out) == ["Fallback", "CycleDone"]
    assert out[0].cause == "rejected" and out[0].detail == "capacity_exceeded: robots meet in the aisle"


def test_replan_success_submits_new_plan():
    """Successful replan submits a new plan to the gate."""
    c = make(max_replan_attempts=3)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)
    c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "robots meet in the aisle"), 2)
    # Simulate successful replan
    out = c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=3)
    assert kinds(out) == ["SubmitPlan"]
    assert out[0].attempt == 1
    assert c.state == "waiting_for_verdict"


def test_replan_failure_falls_back():
    """Failed replan (LLM error) falls back."""
    c = make(max_replan_attempts=3)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)
    c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "robots meet in the aisle"), 2)
    # Simulate failed replan
    out = c.on_replan_result(success=False, plan=None, error_reason=R.LLM_TIMEOUT, error_detail="timeout", now=3)
    assert kinds(out) == ["Fallback", "CycleDone"]
    assert out[0].cause == R.LLM_TIMEOUT


def test_failed_turn_falls_back():
    c = make(turn_timeout_s=10)
    c.start_cycle("m", "ep", 0)
    assert c.tick(9.9) == []
    out = c.tick(10.0)
    assert kinds(out) == ["Fallback", "CycleDone"] and out[0].cause == R.LLM_TIMEOUT

    c = make()
    c.start_cycle("m", "ep", 0)
    assert c.on_turn_failed("robot_b", R.LLM_ERROR, "not its turn", 1) == []
    out = c.on_turn_failed("robot_a", R.LLM_MALFORMED, "bad json", 1)
    assert out[0].cause == R.LLM_MALFORMED and "robot_a: bad json" in out[0].detail


def test_skipped_turn_is_a_discussion():
    c = make()
    c.start_cycle("m", "ep", 0)
    out = c.on_turn(turn(c, "discussion", text="I skip this turn: I could not produce a valid plan."), 1)
    assert kinds(out) == ["SendPrompt"] and out[0].next_speaker == "robot_b"


def test_verdict_timeout_falls_back():
    c = make(verdict_timeout_s=5)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c, now=2)
    assert c.tick(6.9) == []
    out = c.tick(7.0)
    assert kinds(out) == ["Fallback", "CycleDone"] and out[0].cause == "no_verdict"


def test_ignores_out_of_turn_and_stale_messages():
    c = make()
    c.start_cycle("m", "ep", 0)
    assert c.on_turn(Turn("tx-1", 1, "proposal", "robot_b", "", TAKE), 1) == []   # not robot_b's turn
    assert c.on_turn(Turn("tx-0", 1, "proposal", "robot_a", "", A_ONLY), 1) == []  # other transaction
    assert c.on_turn(Turn("tx-1", 1, "prompt", "coordinator"), 1) == []            # its own prompt
    assert c.on_verdict(Verdict("tx-1", "APPROVE"), 1) == []                       # no plan sent yet
    assert c.next_speaker == "robot_a" and c.view.change == 0
    agree_on_take_turns(c)
    assert c.on_verdict(Verdict("tx-0", "APPROVE"), 2) == []


def test_pause_and_resume_world_and_positions():
    c = make(pause_world=True)
    c.positions = {"robot_a": "room_w_s", "robot_b": "aisle_e->room_e"}
    out = c.start_cycle("m", "ep", 0)
    assert isinstance(out[0], PauseWorld) and "robot_a at room_w_s; robot_b at aisle_e->room_e" in out[1].text
    agree_on_take_turns(c)
    out = c.on_verdict(Verdict("tx-1", "APPROVE"), 2)
    assert isinstance(out[-1], ResumeWorld)


def test_new_mission_replaces_running_one():
    ids = iter(["tx-1", "tx-2"])
    c = Coordinator(ProtocolConfig(team=("robot_a", "robot_b")), new_id=lambda: next(ids))
    c.start_cycle("m", "ep", 0)
    c.on_turn(turn(c, "proposal", A_ONLY), 1)
    out = c.start_cycle("m2", "ep", 2)
    assert c.transaction_id == "tx-2" and out[0].round == 1 and out[0].next_speaker == "robot_a"
    assert c.view.change == 0 and [e.kind for e in c.transcript] == ["prompt"]


def test_config_checks():
    for bad in (dict(team=()), dict(team=("a", "a")), dict(team=("coordinator",)), dict(team=("a",), max_rounds=0)):
        with pytest.raises(ValueError):
            ProtocolConfig(**bad)


def test_hold_targets_and_fallback_plan():
    spawns = {"robot_a": "room_w_s", "robot_b": "room_e_n", "robot_c": "room_w_n"}
    t = hold_targets(list(spawns), {"robot_a": "aisle_mid", "robot_b": "aisle_e->room_e"}, spawns)
    assert t == {"robot_a": "aisle_mid", "robot_b": "room_e", "robot_c": "room_w_n"}
    plan = fallback_plan(["robot_a"], {}, spawns)
    assert plan.assignments[0].tasks[0].action.value == "hold_position"
    assert plan.assignments[0].tasks[0].target == "room_w_s"


def test_opening_prompt_gives_start_positions_until_team_state_reports():
    starts = {"robot_a": "room_w_s", "robot_b": "room_e_n"}
    c = Coordinator(ProtocolConfig(team=("robot_a", "robot_b")), new_id=lambda: "tx-1", start_nodes=starts)
    out = c.start_cycle("m", "ep", 0)
    assert "Robots now (start positions): robot_a at room_w_s; robot_b at room_e_n." in out[0].text
    c.positions = {"robot_a": "aisle_mid"}
    out = c.start_cycle("m2", "ep", 1)
    assert "Robots now: robot_a at aisle_mid; robot_b at room_e_n (start position)." in out[0].text
    c.positions = {"robot_a": "aisle_mid", "robot_b": "room_e"}
    out = c.start_cycle("m3", "ep", 2)
    assert "Robots now: robot_a at aisle_mid; robot_b at room_e." in out[0].text


def test_no_positions_line_without_any_source():
    c = make()
    assert "Robots now" not in c.start_cycle("m", "ep", 0)[0].text


def goal_mission(a="room_e", b="room_w"):
    return Mission("swap", {"robot_a": Goal(a, (a,)), "robot_b": Goal(b, (b,))})


def test_goals_in_opening_prompt():
    out = make().start_cycle(goal_mission("room_e_s"), "ep", 0)
    assert "Mission: swap\nGoals: robot_a -> room_e_s; robot_b -> room_w." in out[0].text


def test_agreed_plan_missing_a_goal_never_reaches_the_gate():
    """The agents check their own goals; this is the coordinator's safety net."""
    c = make()
    c.start_cycle(goal_mission("room_e_s"), "ep", 0)          # TAKE_TURNS ends robot_a at room_e
    out = agree_on_take_turns(c)
    assert kinds(out) == ["Fallback", "CycleDone"] and out[0].cause == "mission_mismatch"
    assert "robot_a's last task ends at room_e," in out[0].detail
    c.start_cycle(goal_mission(), "ep", 1)
    assert kinds(agree_on_take_turns(c)) == ["SubmitPlan"]


def test_replan_carries_the_mission_goals():
    """The replanner is told the goals, so that its fix keeps them."""
    c = make(max_replan_attempts=3)
    c.start_cycle(goal_mission(), "ep", 0)
    agree_on_take_turns(c)
    out = c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "robots meet in the aisle"), 2)
    assert kinds(out) == ["StartReplan"]
    assert out[0].goals == "Goals: robot_a -> room_e; robot_b -> room_w."
    assert set(out[0].goal_map) == {"robot_a", "robot_b"} and out[0].goal_map["robot_b"].place == "room_w"


def test_three_robots():
    import copy
    c = Coordinator(ProtocolConfig(team=("robot_a", "robot_b", "robot_c")), new_id=lambda: "tx-3")
    c.start_cycle("m", "ep", 0)
    c_part = {"robot_id": "robot_c", "tasks": [{"task_id": "c1", "action": "hold_position", "target": "room_w_n",
                                                "priority": 3, "justification": "stay", "depends_on": []}]}
    full = copy.deepcopy(TAKE_TURNS)
    full["assignments"].append(c_part)
    c.on_turn(turn(c, "proposal", A_ONLY), 1)
    c.on_turn(turn(c, "proposal", TAKE), 2)
    c.on_turn(turn(c, "proposal", TP(full)), 3)               # robot_c: complete, accepted by robot_c
    c.on_turn(turn(c, "agreement"), 4)                        # robot_a
    out = c.on_turn(turn(c, "agreement"), 5)                  # robot_b
    assert kinds(out) == ["SubmitPlan"] and [a.robot_id for a in out[0].plan.assignments] == ["robot_a", "robot_b", "robot_c"]


def test_cycle_timeout_caps_the_whole_cycle():
    c = make(cycle_timeout_s=100, turn_timeout_s=60)
    c.start_cycle("m", "ep", 0)
    c.on_turn(turn(c, "discussion"), 50)       # each turn on time...
    assert c.tick(99) == []
    out = c.tick(100)                          # ...but the cycle as a whole is over
    assert kinds(out) == ["Fallback", "CycleDone"] and out[0].cause == "cycle_timeout"


def test_stats():
    c = make(max_replan_attempts=0)   # disable replan so REJECT falls back immediately
    c.start_cycle("m", "ep", 0)
    c.on_turn(turn(c, "discussion", text="I skip this turn: my language model failed. Problem: x"), 1)
    c.on_turn(turn(c, "proposal", TP(part(B_NOW))), 2)
    c.on_turn(turn(c, "proposal", A_ONLY), 3)
    c.on_turn(turn(c, "agreement"), 4)
    s = c.stats()
    assert s["outcome"] == "" and c.state == "waiting_for_verdict"
    assert (s["turns"], s["proposals"], s["agreements"], s["skips"], s["discussions"], s["plan_versions"]) == (4, 2, 1, 1, 0, 2)
    c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "aisle"), 5)
    s = c.stats()
    assert s["outcome"] == "fallback" and s["cause"] == "rejected" and s["rounds"] == 2


# ---- Replan edge cases ----

def test_replan_timeout_falls_back():
    """Replan timeout triggers fallback."""
    c = make(max_replan_attempts=3, replan_timeout_s=10)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)
    c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "aisle"), 2)
    assert c.state == "replanning"
    # Time passes without replan result
    assert c.tick(11) == []  # just before deadline (started at 2, deadline at 12)
    out = c.tick(12)
    assert kinds(out) == ["Fallback", "CycleDone"]
    assert out[0].cause == R.LLM_TIMEOUT  # replan timeout uses same cause as turn timeout


def test_multiple_replan_attempts_then_fallback():
    """Multiple replan attempts, all rejected, eventually falls back."""
    c = make(max_replan_attempts=2)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)

    # First REJECT -> replan attempt 1
    out = c.on_verdict(Verdict("tx-1", "REJECT", ("r1",), "reason1", constraints_json="[]"), 2)
    assert kinds(out) == ["StartReplan"] and out[0].attempt == 1

    # Replan succeeds, submit new plan
    out = c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=3)
    assert kinds(out) == ["SubmitPlan"] and out[0].attempt == 1

    # Second REJECT -> replan attempt 2
    out = c.on_verdict(Verdict("tx-1", "REJECT", ("r2",), "reason2", constraints_json="[]", attempt=1), 4)
    assert kinds(out) == ["StartReplan"] and out[0].attempt == 2

    # Replan succeeds again
    out = c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=5)
    assert kinds(out) == ["SubmitPlan"] and out[0].attempt == 2

    # Third REJECT -> no more attempts, fallback
    out = c.on_verdict(Verdict("tx-1", "REJECT", ("r3",), "reason3", constraints_json="[]", attempt=2), 6)
    assert kinds(out) == ["Fallback", "CycleDone"]
    assert out[0].cause == "rejected"


def test_replan_ignores_wrong_state():
    """on_replan_result is ignored if not in replanning state."""
    c = make()
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)
    # Still in waiting_for_verdict, not replanning
    out = c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=3)
    assert out == []
    assert c.state == "waiting_for_verdict"


def test_approve_after_replan():
    """Plan is approved after a replan attempt."""
    c = make(max_replan_attempts=3)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)

    # First REJECT
    c.on_verdict(Verdict("tx-1", "REJECT", ("r1",), "reason"), 2)

    # Replan succeeds
    c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=3)

    # This time APPROVE
    out = c.on_verdict(Verdict("tx-1", "APPROVE", attempt=1), 4)
    assert out == [CycleDone("tx-1", "approved")]
    assert c.state == "done"


def test_modify_after_replan():
    """Plan is modified (accepted with changes) after a replan attempt."""
    c = make(max_replan_attempts=3)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)

    # First REJECT
    c.on_verdict(Verdict("tx-1", "REJECT", ("r1",), "reason"), 2)

    # Replan succeeds
    c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=3)

    # MODIFY
    out = c.on_verdict(Verdict("tx-1", "MODIFY", attempt=1), 4)
    assert out == [CycleDone("tx-1", "approved")]
    assert c.state == "done"


def test_replan_preserves_transaction_id():
    """Transaction ID stays consistent across replan attempts."""
    c = make(max_replan_attempts=3)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c)

    original_tx = c.transaction_id

    # REJECT -> replan
    out = c.on_verdict(Verdict("tx-1", "REJECT", ("r1",), "reason"), 2)
    assert out[0].transaction_id == original_tx

    # Replan succeeds
    out = c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=3)
    assert out[0].transaction_id == original_tx
    assert c.transaction_id == original_tx


def test_cycle_timeout_during_replan():
    """Cycle timeout fires even during replan."""
    c = make(max_replan_attempts=3, cycle_timeout_s=50, replan_timeout_s=100)
    c.start_cycle("m", "ep", 0)
    agree_on_take_turns(c, now=10)

    # REJECT at t=20
    c.on_verdict(Verdict("tx-1", "REJECT", ("r1",), "reason"), 20)
    assert c.state == "replanning"

    # Cycle timeout at t=50 (before replan timeout at t=120)
    out = c.tick(50)
    assert kinds(out) == ["Fallback", "CycleDone"]
    assert out[0].cause == "cycle_timeout"


def _rejected_with_goals(goals_mission):
    c = make(max_replan_attempts=3)
    c.start_cycle(goals_mission, "ep", 0)
    agree_on_take_turns(c)
    c.on_verdict(Verdict("tx-1", "REJECT", ("capacity_exceeded",), "robots meet in the aisle"), 2)
    return c


def test_replanned_plan_that_gives_up_the_mission_never_reaches_the_gate():
    """A replan that holds every robot where it is passes the gate, but does not do the mission."""
    c = _rejected_with_goals(goal_mission())
    hold = fallback_plan(("robot_a", "robot_b"), {}, {"robot_a": "room_w_s", "robot_b": "room_e_n"})
    out = c.on_replan_result(success=True, plan=hold, error_reason="", error_detail="", now=3)
    assert kinds(out) == ["Fallback", "CycleDone"] and out[0].cause == "mission_mismatch"
    assert out[0].detail.startswith("replanned plan: ") and "robot_a's last task ends at room_w_s" in out[0].detail


def test_replanned_plan_that_keeps_the_goals_is_submitted():
    c = _rejected_with_goals(goal_mission())
    out = c.on_replan_result(success=True, plan=TAKE, error_reason="", error_detail="", now=3)
    assert kinds(out) == ["SubmitPlan"] and out[0].attempt == 1
