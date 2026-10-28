"""Development node: print every plan on /mrs/proposed_plan and say whether it is valid.

For watching what the planner sends while debugging. It only prints to the terminal;
recording for audit and analysis is Module 7 (mrs_telemetry).

  ros2 run mrs_common plan_echo
"""
from __future__ import annotations

import rclpy
from pydantic import ValidationError
from rclpy.node import Node

from mrs_msgs.msg import ProposedPlan

from .. import names as N
from ..convert import plan_from_msg
from ..qos import qos_for


def describe(msg: ProposedPlan) -> tuple[bool, str]:
    """(is_valid, one-line summary) for a ProposedPlan. Pure function, easy to test."""
    txn = msg.transaction_id[:8]
    try:
        plan = plan_from_msg(msg)
    except ValidationError as e:
        first = e.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return False, f"INVALID plan txn={txn}: {where}: {first['msg']}"
    except ValueError as e:
        return False, f"INVALID plan txn={txn}: {e}"
    tasks = {a.robot_id: [f"{t.action.value}:{t.target}" for t in a.tasks] for a in plan.assignments}
    return True, f"VALID plan txn={txn} attempt={msg.attempt} {tasks}"


class PlanEcho(Node):
    def __init__(self):
        super().__init__("plan_echo")
        self.create_subscription(ProposedPlan, N.PROPOSED_PLAN, self.on_plan, qos_for(N.PROPOSED_PLAN))

    def on_plan(self, msg: ProposedPlan) -> None:
        ok, text = describe(msg)
        if ok:
            self.get_logger().info(text)
        else:
            self.get_logger().error(text)


def main(args=None) -> None:
    rclpy.init(args=args)
    node = PlanEcho()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
