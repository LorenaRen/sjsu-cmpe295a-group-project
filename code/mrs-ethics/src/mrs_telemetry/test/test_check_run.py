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


def write_events(run, counts, end=True):
    lines = [{"type": "telemetry_start", "t": None}]
    kinds = {N.MISSION: "mission", N.DIALOGUE: "dialogue", N.PROPOSED_PLAN: "proposed_plan", N.VERDICT: "verdict",
             N.VALIDATED_PLAN: "validated_plan"}
    for topic, kind in kinds.items():
        lines += [{"type": kind, "t": 1.0}] * counts.get(topic, 0)
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
    assert main([str(good_run(tmp_path / "again"))]) == 0


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
