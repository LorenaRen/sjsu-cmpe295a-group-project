import json
import sqlite3

import yaml

from mrs_common import names as N

from mrs_telemetry import run_folder as RF
from mrs_telemetry.check_run import ERROR, WARN, check_run, format_report, main
from mrs_telemetry.topics import record_topics

ROBOTS = ["robot_a", "robot_b"]


def write_bag(run, counts: dict, *, files=("bag_0.mcap",), metadata=True):
    bag = run / RF.BAG
    bag.mkdir(parents=True)
    for f in files:
        (bag / f).write_bytes(b"x" * 1000)
    if metadata:
        info = {"version": 9, "storage_identifier": "mcap", "duration": {"nanoseconds": 61_500_000_000},
                "message_count": sum(counts.values()), "relative_file_paths": list(files),
                "topics_with_message_count": [
                    {"topic_metadata": {"name": t, "type": "x/msg/Y", "serialization_format": "cdr"},
                     "message_count": n} for t, n in counts.items()]}
        (bag / "metadata.yaml").write_text(yaml.safe_dump({"rosbag2_bagfile_information": info}))


def good_counts():
    counts = {t: 0 for t in record_topics(ROBOTS)}
    counts.update({N.CLOCK: 600, "/tf_static": 6, "/tf": 300, N.MISSION: 1, N.DIALOGUE: 8, N.PROPOSED_PLAN: 1,
                   N.VERDICT: 1, N.VALIDATED_PLAN: 1})
    for r in ROBOTS:
        counts.update({f"/{r}/scan": 600, f"/{r}/odom": 1800, f"/{r}/ground_truth": 1800})
    return counts


FIELDS = {"mission": {"t": 1.0}, "dialogue": {"t": 1.2}, "proposed_plan": {"t": 1.5, "attempt": 0},
          "verdict": {"t": 1.6, "verdict": "APPROVE"},
          "validated_plan": {"t": 1.6, "source_verdict": "APPROVE", "plan": "robot_a: a1 navigate room_e"}}


def write_events(run, counts, end=True, extra=()):
    lines = [{"type": "telemetry_start", "t": None}]
    kinds = {N.MISSION: "mission", N.DIALOGUE: "dialogue", N.PROPOSED_PLAN: "proposed_plan", N.VERDICT: "verdict",
             N.VALIDATED_PLAN: "validated_plan"}
    for topic, kind in kinds.items():
        lines += [{"type": kind, **FIELDS[kind]}] * counts.get(topic, 0)
    lines += list(extra)
    if end:
        lines.append({"type": "telemetry_end", "t": 60.0})
    (run / RF.EVENTS).write_text("".join(json.dumps(x) + "\n" for x in lines))


def good_run(tmp_path, **kw):
    run = tmp_path / "dev" / "r1"
    RF.update_run_json(run, "git", {"commit": "a" * 40, "dirty": False})
    RF.update_run_json(run, "recording", {"robots": ROBOTS, "scan": True})
    RF.update_run_json(run, "sim", {"record_gz_log": True})
    RF.update_run_json(run, "planner", {"backend": "fake"})
    counts = good_counts()
    write_bag(run, counts)
    write_events(run, counts)
    (run / RF.GZ_LOG).mkdir()
    (run / RF.GZ_LOG / "state.tlog").write_bytes(b"t" * 2000)
    con = sqlite3.connect(run / RF.LLM_LOG)
    con.execute("CREATE TABLE calls (id INTEGER)")
    con.execute("CREATE TABLE cycles (id INTEGER)")
    con.executemany("INSERT INTO calls VALUES (?)", [(i,) for i in range(4)])
    con.execute("INSERT INTO cycles VALUES (1)")
    con.commit()
    con.close()
    return run


def levels(rep, level):
    return [c.text for c in rep.checks if c.level == level]


def test_a_complete_run_is_ok(tmp_path):
    rep = check_run(good_run(tmp_path))
    assert rep.ok, format_report(rep)
    assert rep.duration_s == 61.5 and rep.topics[N.CLOCK] == 600
    assert any("4 LLM calls, 1 planning cycles" in c.text for c in rep.checks)
    warns = " ".join(levels(rep, WARN))
    assert "Module 6" in warns and "Module 3" in warns      # empty topics of modules not built yet
    assert rep.outcome["result"] == "approved" and rep.outcome["attempts"] == 1 and rep.outcome["planning_s"] == 0.6
    assert any(c.part == "outcome" and c.text.startswith("approved; 1 plan to the gate (APPROVE)") for c in rep.checks)
    again = good_run(tmp_path / "again")
    assert main([str(again)]) == 0
    saved = json.loads((again / RF.CHECK).read_text())
    assert saved["ok"] is True and saved["outcome"]["result"] == "approved" and "checked" in saved
    other = good_run(tmp_path / "third")
    assert main([str(other), "--no-write"]) == 0 and not (other / RF.CHECK).exists()


def test_topics_nobody_published_are_listed(tmp_path):
    """rosbag2 leaves a listed topic out of the bag until someone publishes it."""
    run = good_run(tmp_path)
    counts = {t: n for t, n in good_counts().items() if n > 0}      # only what was published
    (run / RF.BAG / "metadata.yaml").unlink()
    for f in (run / RF.BAG).iterdir():
        f.unlink()
    (run / RF.BAG).rmdir()
    write_bag(run, counts)
    RF.update_run_json(run, "recording", {"robots": ROBOTS, "scan": True, "topics": record_topics(ROBOTS)})
    rep = check_run(run)
    assert rep.ok
    warns = " ".join(levels(rep, WARN))
    assert "/robot_a/world_state" in warns and "world state (Module 3)" in warns and "/mrs/task_status" in warns


def test_fallback_outcome_and_replans(tmp_path):
    run = good_run(tmp_path)
    counts = good_counts()
    counts.update({N.PROPOSED_PLAN: 0, N.VERDICT: 0, N.VALIDATED_PLAN: 0})
    write_events(run, counts, extra=[
        {"type": "proposed_plan", "t": 2.0, "attempt": 0}, {"type": "verdict", "t": 2.1, "verdict": "REJECT"},
        {"type": "proposed_plan", "t": 9.0, "attempt": 1}, {"type": "verdict", "t": 9.1, "verdict": "REJECT"},
        {"type": "validated_plan", "t": 9.2, "source_verdict": "FALLBACK", "plan": "robot_a: a1 hold_position room_w_s"},
        {"type": "episode_event", "t": 9.2, "kind": "planning_exhausted",
         "detail": {"transaction_id": "tx", "cause": "rejected", "detail": "capacity_exceeded: ..."}}])
    o = check_run(run).outcome
    assert o["result"] == "fallback" and o["cause"] == "rejected" and o["verdicts"] == ["REJECT", "REJECT"]
    assert (o["attempts"], o["replans"]) == (2, 1)


def test_killed_recorder(tmp_path):
    run = good_run(tmp_path)
    (run / RF.BAG / "metadata.yaml").unlink()
    rep = check_run(run)
    assert not rep.ok and "ros2 bag reindex" in levels(rep, ERROR)[0]


def test_missing_required_topic_and_mission_hint(tmp_path):
    run = tmp_path / "r"
    RF.update_run_json(run, "recording", {"robots": ROBOTS})
    counts = good_counts()
    counts[N.MISSION] = 0
    del counts["/robot_b/odom"]
    write_bag(run, counts)
    write_events(run, counts, end=False)
    rep = check_run(run)
    errors = " ".join(levels(rep, ERROR))
    assert "/mrs/mission: no messages" in errors and "was the mission sent?" in errors
    assert "not recorded at all" in errors and "/robot_b/odom" in errors
    assert any("did not stop cleanly" in w for w in levels(rep, WARN))


def test_events_disagreeing_with_bag_and_missing_gz_log(tmp_path):
    run = good_run(tmp_path)
    counts = good_counts()
    counts[N.DIALOGUE] = 5
    write_events(run, counts)
    (run / RF.GZ_LOG / "state.tlog").unlink()
    rep = check_run(run)
    assert any("dialogue: 5 events but 8 messages" in w for w in levels(rep, WARN))
    assert any("record_gz_log was on" in e for e in levels(rep, ERROR))


def test_missing_parts(tmp_path):
    run = tmp_path / "empty"
    run.mkdir()
    rep = check_run(run, robots=ROBOTS)
    errors = " ".join(levels(rep, ERROR))
    assert "recorder did not run" in errors and "telemetry_node did not run" in errors
    assert main([str(tmp_path / "nope")]) == 2
    assert main([str(run), "--json"]) == 1


def test_failed_replans_are_counted_from_the_coordinators_lines(tmp_path):
    run = good_run(tmp_path)
    counts = good_counts()
    counts.update({N.PROPOSED_PLAN: 0, N.VERDICT: 0, N.VALIDATED_PLAN: 0})
    write_events(run, counts, extra=[
        {"type": "proposed_plan", "t": 2.0, "attempt": 0}, {"type": "verdict", "t": 2.1, "verdict": "REJECT"},
        {"type": "replan", "phase": "start", "attempt": 1}, {"type": "replan", "phase": "success", "attempt": 1, "latency_ms": 8000},
        {"type": "proposed_plan", "t": 11.0, "attempt": 1}, {"type": "verdict", "t": 11.1, "verdict": "REJECT"},
        {"type": "replan", "phase": "start", "attempt": 2},
        {"type": "replan", "phase": "failed", "attempt": 2, "latency_ms": 12000, "error_reason": "mission_mismatch"},
        {"type": "validated_plan", "t": 24.0, "source_verdict": "FALLBACK", "plan": "hold"},
        {"type": "episode_event", "t": 24.0, "kind": "planning_exhausted", "detail": {"cause": "mission_mismatch"}}])
    rep = check_run(run)
    o = rep.outcome
    assert (o["replans"], o["replans_started"], o["replans_failed"], o["replan_s"]) == (1, 2, 1, 10.0)
    line = next(c.text for c in rep.checks if c.part == "outcome")
    assert line == "fallback: mission_mismatch; 2 plans to the gate (REJECT, REJECT), 2 replans, 1 failed, 10.0 s each, 23.0 s sim time"


def test_a_replan_refused_by_the_goal_check_counts_as_failed(tmp_path):
    # Run 20261126-142200: the replan came back (replan_success), but its plan missed a robot,
    # so the goal check ended the cycle before the gate.
    run = good_run(tmp_path)
    counts = good_counts()
    counts.update({N.PROPOSED_PLAN: 0, N.VERDICT: 0, N.VALIDATED_PLAN: 0})
    write_events(run, counts, extra=[
        {"type": "proposed_plan", "t": 2.0, "attempt": 0}, {"type": "verdict", "t": 2.1, "verdict": "REJECT"},
        {"type": "replan", "phase": "start", "attempt": 1},
        {"type": "replan", "phase": "success", "attempt": 1, "latency_ms": 4900},
        {"type": "validated_plan", "t": 9.0, "source_verdict": "FALLBACK", "plan": "hold"},
        {"type": "episode_event", "t": 9.0, "kind": "planning_exhausted",
         "detail": {"cause": "mission_mismatch", "detail": "replanned plan: robot_b has no task"}}])
    rep = check_run(run)
    assert rep.outcome["replans_refused"] == 1
    line = next(c.text for c in rep.checks if c.part == "outcome")
    assert line == ("fallback: mission_mismatch; 1 plan to the gate (REJECT), 1 replan, "
                    "1 failed (1 by the goal check), 4.9 s each, 8.0 s sim time")


def test_a_first_plan_missing_a_goal_is_not_a_failed_replan(tmp_path):
    run = good_run(tmp_path)
    counts = good_counts()
    counts.update({N.VALIDATED_PLAN: 0})
    write_events(run, counts, extra=[
        {"type": "replan", "phase": "start", "attempt": 1}, {"type": "replan", "phase": "success", "attempt": 1},
        {"type": "validated_plan", "t": 9.0, "source_verdict": "FALLBACK", "plan": "hold"},
        {"type": "episode_event", "t": 9.0, "kind": "planning_exhausted",
         "detail": {"cause": "mission_mismatch", "detail": "robot_b has no task"}}])
    assert "replans_refused" not in check_run(run).outcome


def test_repaired_replans_are_counted(tmp_path):
    run = good_run(tmp_path)
    counts = good_counts()
    counts.update({N.PROPOSED_PLAN: 0, N.VERDICT: 0, N.VALIDATED_PLAN: 0})
    write_events(run, counts, extra=[
        {"type": "proposed_plan", "t": 2.0, "attempt": 0}, {"type": "verdict", "t": 2.1, "verdict": "REJECT"},
        {"type": "replan", "phase": "start", "attempt": 1},
        {"type": "replan", "phase": "success", "attempt": 1, "latency_ms": 9000, "repairs": 1},
        {"type": "proposed_plan", "t": 11.0, "attempt": 1}, {"type": "verdict", "t": 11.1, "verdict": "APPROVE"},
        {"type": "validated_plan", "t": 11.2, "source_verdict": "APPROVE", "plan": "p"}])
    rep = check_run(run)
    assert rep.outcome["replans_repaired"] == 1
    line = next(c.text for c in rep.checks if c.part == "outcome")
    assert "1 replan, 1 repaired, 9.0 s each" in line
