"""Topic / frame naming conventions. Pure Python."""
import pytest

from mrs_common import names as N
from mrs_common.ids import is_transaction_id, new_episode_id, new_transaction_id
from mrs_common.qos import qos_class


def test_robot_topics():
    assert N.robot_topic("robot_a", N.SCAN) == "/robot_a/scan"
    assert N.robot_topic("robot_b", N.CMD_VEL_NOMINAL) == "/robot_b/cmd_vel_nominal"


@pytest.mark.parametrize("bad", ["Robot_A", "robot-a", "1robot", "", "robot a", "/robot_a"])
def test_invalid_robot_ids(bad):
    with pytest.raises(ValueError):
        N.robot_topic(bad, N.SCAN)


def test_unknown_topic_name():
    with pytest.raises(ValueError):
        N.robot_topic("robot_a", "laser")


def test_frames_are_prefixed():
    assert N.odom_frame("robot_a") == "robot_a/odom"
    assert N.base_frame("robot_b") == "robot_b/base_link"
    assert N.lidar_frame("robot_c") == "robot_c/lidar"
    assert N.camera_frame("robot_d") == "robot_d/camera"


def test_camera_topics_are_optional():
    assert N.robot_topic("robot_a", N.CAMERA_IMAGE) == "/robot_a/camera/image_raw"
    with_cam = N.all_topics(N.robot_ids(2), camera=True)
    assert len(with_cam) == len(N.all_topics(N.robot_ids(2))) + 2 * len(N.CAMERA_TOPIC_TYPES)
    assert with_cam["/robot_b/camera/camera_info"] == "sensor_msgs/msg/CameraInfo"


def test_all_topics():
    topics = N.all_topics(N.robot_ids(3))
    assert len(topics) == 3 * len(N.ROBOT_TOPIC_TYPES) + len(N.TEAM_TOPIC_TYPES)
    assert topics["/mrs/proposed_plan"] == "mrs_msgs/msg/ProposedPlan"
    assert topics["/robot_c/world_state"] == "mrs_msgs/msg/WorldState"
    assert all(t.startswith("/mrs/") for t in N.TEAM_TOPIC_TYPES)


def test_robot_ids():
    assert N.robot_ids(2) == ["robot_a", "robot_b"]
    with pytest.raises(ValueError):
        N.robot_ids(0)


@pytest.mark.parametrize("topic, cls", [
    ("/robot_a/scan", "sensor"), ("/robot_a/odom", "sensor"), ("/robot_a/ground_truth", "sensor"),
    ("/robot_a/camera/image_raw", "sensor"), ("/robot_b/camera/camera_info", "sensor"),
    ("/robot_a/cmd_vel", "command"), ("/robot_b/cmd_vel_nominal", "command"),
    (N.MISSION, "latched"), (N.VALIDATED_PLAN, "latched"),
    (N.PROPOSED_PLAN, "decision"), (N.VERDICT, "decision"), ("/robot_a/world_state", "decision"),
])
def test_qos_classes(topic, cls):
    assert qos_class(topic) == cls


def test_ids():
    t = new_transaction_id()
    assert is_transaction_id(t) and t != new_transaction_id()
    assert not is_transaction_id("not-a-uuid")
    e = new_episode_id("S1 Exit Blocking", seed=7)
    assert e.startswith("s1-exit-blocking-s7-")


# ---- waypoint-graph edges ----------------------------------------------------------
def test_edge_id_is_undirected_and_canonical():
    assert N.edge_id("room_3", "corridor_1") == "corridor_1--room_3"
    assert N.edge_id("corridor_1", "room_3") == N.edge_id("room_3", "corridor_1")
    assert N.edge_nodes("corridor_1--room_3") == ("corridor_1", "room_3")


@pytest.mark.parametrize("a, b", [("room_3", "room_3"), ("", "room_3"), ("a--b", "c"), ("a->b", "c")])
def test_edge_id_rejects_bad_nodes(a, b):
    with pytest.raises(ValueError):
        N.edge_id(a, b)


@pytest.mark.parametrize("bad", ["room_3", "room_3--corridor_1", "a--b--c", "--room_3", ""])
def test_edge_nodes_rejects_non_canonical(bad):
    with pytest.raises(ValueError):
        N.edge_nodes(bad)


def test_edge_from_world_state_at():
    assert N.edge_from_at("room_3->corridor_1") == "corridor_1--room_3"   # direction dropped
    assert N.edge_from_at("corridor_1->room_3") == "corridor_1--room_3"
    assert N.edge_from_at("room_3") is None                                # at a node
    assert N.edge_from_at("") is None
