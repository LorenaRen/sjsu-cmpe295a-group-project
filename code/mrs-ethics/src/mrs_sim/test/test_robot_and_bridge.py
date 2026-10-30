"""Robot SDF and bridge config are namespaced per robot (Module 2). Pure Python."""
import xml.etree.ElementTree as ET

import pytest
import yaml

from mrs_common import names as N
from mrs_sim.bridge import bridge_entries, bridge_yaml
from mrs_sim.robot_model import CAMERA_XYZ, LIDAR_XYZ, robot_color, robot_sdf, sensor_frames


def _model(robot, camera=True):
    return ET.fromstring(robot_sdf(robot, camera)).find("model")


def test_robot_sdf_is_namespaced():
    m = _model("robot_b")
    assert m.get("name") == "robot_b"
    topics = {e.text for e in m.iter("topic")}
    assert topics == {"/robot_b/scan", "/robot_b/camera/image_raw", "/robot_b/cmd_vel"}
    dd = m.find("plugin[@name='gz::sim::systems::DiffDrive']")
    assert dd.findtext("odom_topic") == N.robot_topic("robot_b", N.ODOM)
    assert dd.findtext("frame_id") == N.odom_frame("robot_b")
    assert dd.findtext("child_frame_id") == N.base_frame("robot_b")
    gt = m.find("plugin[@name='gz::sim::systems::OdometryPublisher']")
    assert gt.findtext("odom_topic") == N.robot_topic("robot_b", N.GROUND_TRUTH)
    assert gt.findtext("odom_frame") == N.MAP_FRAME
    frames = {e.text for e in m.iter("gz_frame_id")}
    assert frames == {N.lidar_frame("robot_b"), N.camera_frame("robot_b")}
    assert "$" not in robot_sdf("robot_b"), "unfilled template placeholder"


def test_camera_toggle():
    assert _model("robot_a", camera=False).find("link[@name='camera_link']") is None
    assert _model("robot_a", camera=True).find("link[@name='camera_link']") is not None
    assert len(sensor_frames("robot_a", camera=False)) == 1
    assert len(sensor_frames("robot_a", camera=True)) == 2


def test_sensor_mounts_match_sdf():
    m = _model("robot_a")
    lidar = [float(v) for v in m.find("link[@name='lidar_link']/pose").text.split()[:3]]
    cam = [float(v) for v in m.find("link[@name='camera_link']/pose").text.split()[:3]]
    assert tuple(lidar) == LIDAR_XYZ and tuple(cam) == CAMERA_XYZ


def test_robots_get_different_colors():
    assert len({robot_color(r) for r in N.robot_ids(4)}) == 4


@pytest.mark.parametrize("camera", [True, False])
def test_bridge_entries(camera):
    robots = N.robot_ids(2)
    entries = bridge_entries(robots, camera)
    ros = [e["ros_topic_name"] for e in entries]
    for r in robots:
        for name in (N.SCAN, N.ODOM, N.GROUND_TRUTH, N.CMD_VEL):
            assert N.robot_topic(r, name) in ros
        assert (N.robot_topic(r, N.CAMERA_IMAGE) in ros) == camera
    assert ros.count("/tf") == 2 and ros.count(N.CLOCK) == 1
    cmd = [e for e in entries if e["ros_topic_name"].endswith("/cmd_vel")]
    assert all(e["direction"] == "ROS_TO_GZ" and e["lazy"] is False for e in cmd)
    assert yaml.safe_load(bridge_yaml(robots, camera)) == entries
