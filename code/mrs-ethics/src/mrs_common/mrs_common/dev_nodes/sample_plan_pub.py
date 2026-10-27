"""Development node: publish a bundled sample plan on /mrs/proposed_plan every few seconds.

Stands in for the LLM planner (Module 4) so downstream nodes (e.g. the gatekeeper, Module 5)
can be tested with fixed plans and no API calls.

  ros2 run mrs_common sample_plan_pub
  ros2 run mrs_common sample_plan_pub --ros-args -p sample:=keep_clear_stop.json -p period:=5.0

Parameters
  sample  (str,   default valid_two_robots.json)  file name in mrs_common/samples/.
          Invalid samples are published as raw JSON on purpose, to exercise error handling.
  period  (float, default 2.0)                    seconds between messages
"""
from __future__ import annotations

import rclpy
from pydantic import ValidationError
from rclpy.node import Node

from mrs_msgs.msg import ProposedPlan

from .. import load_sample, names as N
from ..convert import make_proposed_plan
from ..ids import new_episode_id
from ..plan_schema import plan_from_json
from ..qos import qos_for

# Valid plan used to fill the other fields when an invalid sample is published as raw JSON.
_CARRIER_SAMPLE = "valid_two_robots.json"


class SamplePlanPublisher(Node):
    def __init__(self):
        super().__init__("sample_plan_pub")
        self.sample = self.declare_parameter("sample", "valid_two_robots.json").value
        period = float(self.declare_parameter("period", 2.0).value)
        self.raw = load_sample(self.sample)
        try:
            self.plan = plan_from_json(self.raw)
        except ValidationError:
            self.plan = None
            self.get_logger().info(f"{self.sample} is invalid on purpose; publishing the raw JSON")
        self.episode_id = new_episode_id("dev-sample-plan")
        self.attempt = 0
        self.pub = self.create_publisher(ProposedPlan, N.PROPOSED_PLAN, qos_for(N.PROPOSED_PLAN))
        self.create_timer(period, self.tick)

    def build_message(self) -> ProposedPlan:
        plan = self.plan if self.plan is not None else plan_from_json(load_sample(_CARRIER_SAMPLE))
        msg = make_proposed_plan(plan, episode_id=self.episode_id, attempt=self.attempt,
                                 model_name="sample", node=self)
        if self.plan is None:
            msg.plan_json = self.raw
        return msg

    def tick(self) -> None:
        msg = self.build_message()
        self.pub.publish(msg)
        self.get_logger().info(f"published {self.sample} attempt={self.attempt} txn={msg.transaction_id[:8]}")
        self.attempt += 1


def main(args=None) -> None:
    rclpy.init(args=args)
    node = SamplePlanPublisher()
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
