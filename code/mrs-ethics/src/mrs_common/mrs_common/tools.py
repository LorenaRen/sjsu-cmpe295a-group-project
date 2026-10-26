"""Command-line checks installed with the package (Module 1). Plain scripts, not ROS nodes.

  ros2 run mrs_common check_interfaces [--robots 2]   import every message, list canonical topics, validate samples
  ros2 run mrs_common export_plan_schema [-o FILE]    write the TeamPlan JSON Schema (for LLM structured output)

The development nodes (sample_plan_pub, plan_echo) live in mrs_common/dev_nodes/.
"""
from __future__ import annotations

import argparse
import json
import sys

from pydantic import ValidationError

from . import load_sample, names as N
from .plan_schema import plan_from_json, team_plan_json_schema

SAMPLES_VALID = ["valid_two_robots.json", "exit_blocking.json"]
SAMPLES_INVALID = ["invalid_duplicate_task.json", "invalid_unknown_field.json", "invalid_bad_action.json"]


def check_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=check_main.__doc__)
    ap.add_argument("--robots", type=int, default=2)
    args, _ = ap.parse_known_args(argv)
    ok = True
    try:
        import mrs_msgs.msg as m
        classes = ["PerceivedEntity", "WorldState", "TeamState", "DialogueTurn", "ProposedPlan",
                   "EthicalVerdict", "ValidatedPlan", "TaskStatus", "SafetyOverride", "EpisodeEvent"]
        for c in classes:
            getattr(m, c)()
        print(f"[ok] mrs_msgs: {len(classes)} message types import and construct")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"[FAIL] mrs_msgs not usable ({e}). Did you `colcon build` and source install/setup.*?")
    for name in SAMPLES_VALID:
        try:
            plan_from_json(load_sample(name))
            print(f"[ok] sample {name} validates")
        except ValidationError as e:
            ok = False
            print(f"[FAIL] sample {name}: {e}")
    for name in SAMPLES_INVALID:
        try:
            plan_from_json(load_sample(name))
            ok = False
            print(f"[FAIL] sample {name} should have been rejected")
        except ValidationError:
            print(f"[ok] sample {name} is rejected as expected")
    print(f"\nCanonical topics for {args.robots} robot(s):")
    for topic, typ in N.all_topics(N.robot_ids(args.robots)).items():
        print(f"  {topic:32s} {typ}")
    return 0 if ok else 1


def export_schema_main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Export the TeamPlan JSON Schema")
    ap.add_argument("-o", "--output", default="-")
    args, _ = ap.parse_known_args(argv)
    text = json.dumps(team_plan_json_schema(), indent=2)
    if args.output == "-":
        print(text)
    else:
        with open(args.output, "w") as f:
            f.write(text + "\n")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(check_main())
