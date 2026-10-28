"""Per-robot SDF for mrs_bot (Module 2). Pure Python, no ROS imports.

robot_sdf('robot_a') fills templates/mrs_bot.sdf so every Gazebo topic and frame is namespaced
under the robot id. The geometry constants below are the single source of truth: the launch file
publishes the static base_link -> lidar / camera transforms from the same numbers.
"""
from __future__ import annotations

from importlib import resources
from string import Template

from mrs_common import names as N

WHEEL_RADIUS = 0.06       # [m]
WHEEL_SEPARATION = 0.36   # [m] wheel centre to wheel centre
MAX_LINEAR = 0.5          # [m/s] DiffDrive clamps commands to this
MAX_ANGULAR = 1.5         # [rad/s]

# Sensor mounts, relative to base_link (which sits at axle height, WHEEL_RADIUS above the floor).
LIDAR_XYZ = (0.15, 0.0, 0.12)
CAMERA_XYZ = (0.24, 0.0, 0.06)

CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
CAMERA_RATE = 10          # [Hz]

# Body colour per robot slot (RGB 0..1), so robots are easy to tell apart in the GUI and video.
COLORS = [
    (0.95, 0.55, 0.10),  # robot_a orange
    (0.15, 0.45, 0.90),  # robot_b blue
    (0.20, 0.70, 0.30),  # robot_c green
    (0.60, 0.30, 0.80),  # robot_d purple
]


def _template(name: str) -> Template:
    return Template(resources.files("mrs_sim").joinpath("templates", name).read_text())


def robot_color(robot_id: str) -> tuple[float, float, float]:
    """Slot colour from the id letter: robot_a -> COLORS[0], ... (cycles after the palette)."""
    N.validate_robot_id(robot_id)
    idx = ord(robot_id[-1]) - ord("a") if robot_id.startswith("robot_") and len(robot_id) == 7 else 0
    return COLORS[idx % len(COLORS)]


def robot_sdf(robot_id: str, camera: bool = True) -> str:
    """SDF model string for one robot."""
    N.validate_robot_id(robot_id)
    camera_block = ""
    if camera:
        camera_block = _template("camera.sdf").substitute(
            robot=robot_id,
            camera_x=CAMERA_XYZ[0], camera_z=CAMERA_XYZ[2],
            camera_width=CAMERA_WIDTH, camera_height=CAMERA_HEIGHT, camera_rate=CAMERA_RATE,
        )
    return _template("mrs_bot.sdf").substitute(
        robot=robot_id,
        color=" ".join(f"{c:.2f}" for c in robot_color(robot_id)),
        wheel_radius=WHEEL_RADIUS,
        wheel_separation=WHEEL_SEPARATION,
        half_separation=WHEEL_SEPARATION / 2,
        max_linear=MAX_LINEAR,
        max_angular=MAX_ANGULAR,
        lidar_x=LIDAR_XYZ[0], lidar_z=LIDAR_XYZ[2],
        camera_block=camera_block,
    )


def sensor_frames(robot_id: str, camera: bool = True) -> list[tuple[str, str, tuple[float, float, float]]]:
    """(parent, child, xyz) static transforms from base_link to each sensor frame."""
    out = [(N.base_frame(robot_id), N.lidar_frame(robot_id), LIDAR_XYZ)]
    if camera:
        out.append((N.base_frame(robot_id), N.camera_frame(robot_id), CAMERA_XYZ))
    return out
