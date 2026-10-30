"""Start a mrs_sim world with N robots (Module 2).

  ros2 launch mrs_sim sim.launch.py                                  # narrow_aisle, 2 robots, camera on, headless
  ros2 launch mrs_sim sim.launch.py world:=aisle_bypass robots:=3 camera:=false gui:=true
  ros2 launch mrs_sim sim.launch.py world:=/path/to/my_world.sdf    # a .yaml map must sit next to it

Starts: the Gazebo server (and optionally the GUI as a separate process, which macOS requires),
one robot per spawn slot, one ros_gz_bridge for all robots, and the static transforms
map -> <robot>/odom (spawn pose) and <robot>/base_link -> <robot>/lidar, <robot>/camera.
The generated robot SDFs and bridge config are written to a run folder that is printed at start.
"""
import tempfile
from pathlib import Path

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, LogInfo, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from mrs_common import names as N
from mrs_sim.bridge import bridge_yaml
from mrs_sim.robot_model import robot_sdf, sensor_frames
from mrs_sim.world_map import load_world_map

_TRUE = ("true", "1", "yes", "on")


def _setup(context):
    arg = lambda name: LaunchConfiguration(name).perform(context)  # noqa: E731
    camera = arg("camera").lower() in _TRUE
    gui = arg("gui").lower() in _TRUE
    wm, sdf_path = load_world_map(arg("world"))
    robots = N.robot_ids(int(arg("robots")))
    poses = wm.spawn_poses(robots)

    run_dir = Path(arg("run_dir") or tempfile.mkdtemp(prefix=f"mrs_sim_{wm.world}_"))
    run_dir.mkdir(parents=True, exist_ok=True)
    bridge_file = run_dir / "bridge.yaml"
    bridge_file.write_text(bridge_yaml(robots, camera))

    sim_time = {"use_sim_time": True}
    actions = [
        LogInfo(msg=f"[mrs_sim] world={wm.world} robots={robots} camera={camera} gui={gui} run_dir={run_dir}"),
        ExecuteProcess(cmd=["gz", "sim", "-s", "-r", "-v", arg("verbosity"), str(sdf_path)],
                       output="screen", name="gz_server"),
        Node(package="ros_gz_bridge", executable="parameter_bridge", name="mrs_bridge", output="screen",
             parameters=[{"config_file": str(bridge_file), **sim_time}]),
    ]
    if gui:
        actions.append(ExecuteProcess(cmd=["gz", "sim", "-g", "-v", arg("verbosity")], output="screen",
                                      name="gz_gui"))

    for r in robots:
        model_file = run_dir / f"{r}.sdf"
        model_file.write_text(robot_sdf(r, camera))
        p = poses[r]
        actions.append(Node(
            package="ros_gz_sim", executable="create", name=f"spawn_{r}", output="screen",
            arguments=["-world", wm.world, "-file", str(model_file), "-name", r,
                       "-x", str(p.x), "-y", str(p.y), "-z", "0.0", "-Y", str(p.yaw)]))
        # DiffDrive odometry starts at zero where the robot spawns, so map -> odom is the spawn pose.
        actions.append(Node(
            package="tf2_ros", executable="static_transform_publisher", name=f"tf_map_{r}_odom",
            arguments=["--x", str(p.x), "--y", str(p.y), "--yaw", str(p.yaw),
                       "--frame-id", N.MAP_FRAME, "--child-frame-id", N.odom_frame(r)],
            parameters=[sim_time]))
        for parent, child, (x, y, z) in sensor_frames(r, camera):
            actions.append(Node(
                package="tf2_ros", executable="static_transform_publisher",
                name=f"tf_{child.replace('/', '_')}",
                arguments=["--x", str(x), "--y", str(y), "--z", str(z),
                           "--frame-id", parent, "--child-frame-id", child],
                parameters=[sim_time]))
    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("world", default_value="narrow_aisle",
                              description="world name in mrs_sim/worlds, or a path to a .sdf with a .yaml next to it"),
        DeclareLaunchArgument("robots", default_value="2", description="number of robots (robot_a, robot_b, ...)"),
        DeclareLaunchArgument("camera", default_value="true", description="front camera on every robot"),
        DeclareLaunchArgument("gui", default_value="false", description="also open the Gazebo GUI"),
        DeclareLaunchArgument("verbosity", default_value="2", description="gz sim -v level (0-4)"),
        DeclareLaunchArgument("run_dir", default_value="",
                              description="where to write generated robot SDFs + bridge.yaml (default: a temp dir)"),
        OpaqueFunction(function=_setup),
    ])
