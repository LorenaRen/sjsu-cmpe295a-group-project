"""sim_check: confirm a running mrs_sim is healthy (Module 2).

  ros2 run mrs_sim sim_check                       # 2 robots, narrow_aisle, camera expected
  ros2 run mrs_sim sim_check --robots 2 --no-camera --drive

For every robot: are scan / odom / ground_truth (and the camera) arriving, and at what rate;
does ground truth start at the world's spawn pose; and, with --drive, does the robot move
forward when sent a cmd_vel. Exit code 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import math
import sys
import time

from mrs_common import names as N
from mrs_common.qos import qos_for
from mrs_sim.world_map import load_world_map

SPAWN_TOLERANCE = 0.15   # [m]
DRIVE_SPEED = 0.2        # [m/s]
DRIVE_SECONDS = 3.0      # wall time
MIN_DRIVE_DISTANCE = 0.2  # [m]; ideal is speed * time = 0.6 m at real-time factor 1


def _yaw(q) -> float:
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--robots", type=int, default=2)
    ap.add_argument("--world", default="narrow_aisle")
    ap.add_argument("--no-camera", action="store_true", help="the sim was started with camera:=false")
    ap.add_argument("--duration", type=float, default=5.0, help="seconds to listen [wall time]")
    ap.add_argument("--drive", action="store_true", help="also drive each robot forward briefly")
    args = ap.parse_args(argv)

    import rclpy
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import Image, LaserScan

    robots = N.robot_ids(args.robots)
    wm, _ = load_world_map(args.world)
    spawns = wm.spawn_poses(robots)

    rclpy.init()
    node = rclpy.create_node("mrs_sim_check")
    counts: dict[str, int] = {}
    latest_gt: dict[str, Odometry] = {}
    first_gt: dict[str, Odometry] = {}
    subs = []

    def counter(topic, robot=None):
        counts[topic] = 0

        def cb(msg):
            counts[topic] += 1
            if robot is not None:
                latest_gt[robot] = msg
                first_gt.setdefault(robot, msg)
        return cb

    for r in robots:
        topics = [(N.robot_topic(r, N.SCAN), LaserScan, None),
                  (N.robot_topic(r, N.ODOM), Odometry, None),
                  (N.robot_topic(r, N.GROUND_TRUTH), Odometry, r)]
        if not args.no_camera:
            topics.append((N.robot_topic(r, N.CAMERA_IMAGE), Image, None))
        for t, typ, robot in topics:
            subs.append(node.create_subscription(typ, t, counter(t, robot), qos_for(t)))

    def spin_for(seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            rclpy.spin_once(node, timeout_sec=0.05)

    print(f"listening for {args.duration:.0f} s ...")
    spin_for(args.duration)
    ok = True
    for t, c in counts.items():
        hz = c / args.duration
        good = c > 0
        ok &= good
        print(f"{'[ok]  ' if good else '[FAIL]'} {t:<32} {hz:6.1f} Hz")

    for r in robots:
        gt, sp = first_gt.get(r), spawns[r]
        if gt is None:
            continue
        p = gt.pose.pose.position
        err = math.hypot(p.x - sp.x, p.y - sp.y)
        good = err < SPAWN_TOLERANCE
        ok &= good
        print(f"{'[ok]  ' if good else '[FAIL]'} {r} ground truth at ({p.x:.2f}, {p.y:.2f}), "
              f"spawn slot ({sp.x:.2f}, {sp.y:.2f}), off by {err:.2f} m")

    if args.drive:
        pubs = {r: node.create_publisher(Twist, N.robot_topic(r, N.CMD_VEL), qos_for(N.robot_topic(r, N.CMD_VEL)))
                for r in robots}
        start = {r: latest_gt.get(r) for r in robots}
        cmd = Twist()
        cmd.linear.x = DRIVE_SPEED
        end = time.monotonic() + DRIVE_SECONDS
        while time.monotonic() < end:
            for pub in pubs.values():
                pub.publish(cmd)
            spin_for(0.1)
        for pub in pubs.values():
            pub.publish(Twist())
        spin_for(0.5)
        for r in robots:
            a, b = start.get(r), latest_gt.get(r)
            if a is None or b is None:
                print(f"[FAIL] {r} drive: no ground truth")
                ok = False
                continue
            pa, pb = a.pose.pose.position, b.pose.pose.position
            yaw = _yaw(a.pose.pose.orientation)
            forward = (pb.x - pa.x) * math.cos(yaw) + (pb.y - pa.y) * math.sin(yaw)
            good = forward > MIN_DRIVE_DISTANCE
            ok &= good
            print(f"{'[ok]  ' if good else '[FAIL]'} {r} drove {forward:.2f} m forward "
                  f"(sent {DRIVE_SPEED} m/s for {DRIVE_SECONDS:.0f} s)")

    node.destroy_node()
    rclpy.shutdown()
    print("all checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
