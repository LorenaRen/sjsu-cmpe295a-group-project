import json
from types import SimpleNamespace as NS

from mrs_common import load_sample
from mrs_common import names as N

from mrs_telemetry.events import (EVENT_TOPICS, EventLog, Throttle, make_event, plan_summary, read_events,
                                  stamp_s)

PLAN = load_sample("valid_two_robots.json")
STAMP = NS(sec=12, nanosec=500_000_000)
IDS = dict(episode_id="ep1", transaction_id="tx1", attempt=0, stamp=STAMP)


def turn(**kw):
    base = dict(round=1, kind="discussion", speaker_id="robot_a", next_speaker="", text="", plan_json="", **IDS)
    return NS(**{**base, **kw})


def test_stamp_and_plan_summary():
    assert stamp_s(STAMP) == 12.5 and stamp_s(NS(sec=0, nanosec=0)) is None and stamp_s(None) is None
    s = plan_summary(PLAN)
    assert s.startswith("robot_a: ") and " | robot_b: " in s and "(after " in s
    assert plan_summary("{not json") == "(unreadable plan_json)" and plan_summary("") == ""


def test_every_decision_topic_has_a_handler():
    assert set(EVENT_TOPICS) == {N.MISSION, N.DIALOGUE, N.PROPOSED_PLAN, N.VERDICT, N.VALIDATED_PLAN,
                                 N.TASK_STATUS, N.EPISODE_EVENT, N.SAFETY_OVERRIDE}


def test_dialogue_events():
    e = make_event(N.DIALOGUE, turn(kind="prompt", speaker_id="coordinator", next_speaker="robot_b", round=2,
                                    text="Mission ..."), t=3.0)
    assert e["type"] == "dialogue" and e["summary"] == "round 2: robot_b's turn" and e["next_speaker"] == "robot_b"
    assert e["t"] == 3.0 and e["stamp"] == 12.5 and e["transaction_id"] == "tx1"
    e = make_event(N.DIALOGUE, turn(kind="proposal", plan_json=PLAN, text="I go first"), t=4.0)
    assert e["plan"] == plan_summary(PLAN) and e["summary"].startswith("round 1: robot_a proposal: robot_a:")
    e = make_event(N.DIALOGUE, turn(text="x" * 5000), t=None)
    assert len(e["text"]) == 1500 and e["t"] is None


def test_plan_and_verdict_events():
    p = NS(plan_json=PLAN, model_name="ollama/phi3.5", latency_ms=1234.56, tokens_in=900, tokens_out=150,
           dialogue_digest="3 turns", **IDS)
    e = make_event(N.PROPOSED_PLAN, p, t=5.0)
    assert e["type"] == "proposed_plan" and e["latency_ms"] == 1234.6 and e["model"] == "ollama/phi3.5"
    v = NS(verdict="REJECT", rule_ids=["capacity_exceeded"], risk_score=1.0, rationale="both in the aisle",
           constraints_json=json.dumps([{"kind": "order_before", "text": "t"}]), latency_ms=3.2, **IDS)
    e = make_event(N.VERDICT, v, t=6.0)
    assert e["summary"] == "REJECT (capacity_exceeded)" and e["constraints"] == ["order_before"]
    val = NS(plan_json=PLAN, source_verdict="FALLBACK", **IDS)
    assert make_event(N.VALIDATED_PLAN, val, t=7.0)["summary"].startswith("plan to the robots (FALLBACK)")


def test_status_event_and_override_events():
    ts = NS(robot_id="robot_a", task_id="a1", state="done", waypoint="room_e", detail="", **IDS)
    assert make_event(N.TASK_STATUS, ts, t=1.0)["summary"] == "robot_a a1 done at room_e"
    ev = NS(episode_id="ep1", kind="planning_exhausted", robot_id="", stamp=STAMP,
            detail_json=json.dumps({"reason": "no_agreement"}))
    e = make_event(N.EPISODE_EVENT, ev, t=1.0)
    assert e["summary"] == "planning_exhausted: no_agreement" and e["detail"] == {"reason": "no_agreement"}
    ev.detail_json = "not json"
    assert make_event(N.EPISODE_EVENT, ev, t=1.0)["detail"] == "not json"
    so = NS(robot_id="robot_b", episode_id="ep1", u_nominal=NS(x=0.5, y=0.0, z=0), u_safe=NS(x=0.1, y=0.02, z=0),
            min_distance=0.61234, active_constraints=["pair:robot_a-robot_b"], stamp=STAMP)
    e = make_event(N.SAFETY_OVERRIDE, so, t=1.0)
    assert e["u_safe"] == [0.1, 0.02] and e["min_distance"] == 0.612


def test_throttle():
    th = Throttle(1.0)
    assert th.allow("a", 10.0) == (True, 0)
    assert th.allow("a", 10.3) == (False, 0) and th.allow("a", 10.6) == (False, 0)
    assert th.allow("b", 10.6) == (True, 0)
    assert th.allow("a", 11.0) == (True, 2)
    assert th.allow("a", None) == (True, 0)
    assert th.allow("a", 2.0) == (True, 0)      # time went backwards (sim reset): never stall


def test_event_log_writes_lines_at_once(tmp_path):
    path = tmp_path / "run" / "events.jsonl"
    log = EventLog(path)
    log.note("telemetry_start", None, "start")
    log.record(N.MISSION, NS(data="robot_a: go to room_e."), t=1.0)
    so = NS(robot_id="robot_b", episode_id="", u_nominal=NS(x=0, y=0), u_safe=NS(x=0, y=0), min_distance=1.0,
            active_constraints=[], stamp=None)
    assert log.record(N.SAFETY_OVERRIDE, so, t=2.0) is not None
    assert log.record(N.SAFETY_OVERRIDE, so, t=2.5) is None
    assert log.record(N.SAFETY_OVERRIDE, so, t=3.1)["skipped"] == 1
    events, problems = read_events(path)   # readable before close: every line is flushed
    assert [e["type"] for e in events] == ["telemetry_start", "mission", "safety_override", "safety_override"]
    assert problems == [] and log.counts["safety_override"] == 2
    log.close()
    with open(path, "a") as f:
        f.write("{broken\n")
    events, problems = read_events(path)
    assert len(events) == 4 and len(problems) == 1
