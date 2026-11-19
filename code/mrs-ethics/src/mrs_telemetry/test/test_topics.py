from mrs_common import names as N

from mrs_telemetry.topics import (TF, TF_STATIC, publisher_of, qos_overrides, record_command, record_topics,
                                  required_topics)

ROBOTS = ["robot_a", "robot_b"]


def test_record_topics_cover_every_canonical_topic():
    topics = record_topics(ROBOTS)
    assert set(N.all_topics(ROBOTS)) <= set(topics)
    assert {N.CLOCK, TF, TF_STATIC} <= set(topics)
    assert topics == sorted(set(topics))
    assert not any("camera" in t for t in topics)


def test_camera_and_scan_switches():
    assert "/robot_a/camera/image_raw" in record_topics(ROBOTS, camera=True)
    no_scan = record_topics(ROBOTS, scan=False)
    assert "/robot_a/scan" not in no_scan and "/robot_a/odom" in no_scan
    assert "/robot_a/scan" not in required_topics(ROBOTS, scan=False)


def test_required_topics_are_what_the_current_stack_publishes():
    req = required_topics(ROBOTS)
    assert N.MISSION in req and N.DIALOGUE in req and N.CLOCK in req
    assert "/robot_b/ground_truth" in req
    assert N.TASK_STATUS not in req and "/robot_a/world_state" not in req   # Modules 3 and 6 do not exist yet


def test_latched_topics_are_recorded_transient_local():
    over = qos_overrides(record_topics(ROBOTS))
    assert set(over) == {TF_STATIC, N.MISSION, N.VALIDATED_PLAN}
    assert all(v["durability"] == "transient_local" and v["reliability"] == "reliable" for v in over.values())


def test_record_command():
    cmd = record_command("/r/bag", ["/clock", "/mrs/dialogue"], overrides_file="/r/q.yaml")
    assert cmd[:3] == ["ros2", "bag", "record"]
    assert cmd[cmd.index("-s") + 1] == "mcap" and "--use-sim-time" in cmd
    assert cmd[cmd.index("-o") + 1] == "/r/bag"
    assert cmd[cmd.index("--qos-profile-overrides-path") + 1] == "/r/q.yaml"
    assert cmd[-3:] == ["--topics", "/clock", "/mrs/dialogue"]
    assert "--use-sim-time" not in record_command("/r/bag", ["/clock"], sim_time=False)


def test_publisher_of():
    assert "Module 6" in publisher_of("/robot_a/cmd_vel")
    assert "Module 3" in publisher_of(N.TEAM_STATE)
    assert publisher_of("/something/else") == "unknown"
