"""Reply texts for the narrow_aisle swap: robot_a (room_w_s) to room_e, robot_b (room_e_n) to room_w."""
import json

from mrs_common.plan_schema import SCHEMA_VERSION


def _task(task_id, target, action="navigate", depends_on=(), priority=1):
    return {"task_id": task_id, "action": action, "target": target, "priority": priority,
            "justification": "mission", "depends_on": list(depends_on)}


# Both robots drive into the one-lane aisle at once: S-1 rejects it.
HEAD_ON = {"schema_version": SCHEMA_VERSION, "rationale": "both go now",
           "assignments": [{"robot_id": "robot_a", "tasks": [_task("a1", "room_e")]},
                           {"robot_id": "robot_b", "tasks": [_task("b1", "room_w")]}]}

# robot_b waits in its room until robot_a is through, then crosses.
TAKE_TURNS = {"schema_version": SCHEMA_VERSION, "rationale": "robot_a crosses first, robot_b waits",
              "assignments": [{"robot_id": "robot_a", "tasks": [_task("a1", "room_e")]},
                              {"robot_id": "robot_b", "tasks": [_task("b1", "room_w", depends_on=["a1"], priority=2)]}]}

# A target that is not on the map.
HALLUCINATED = {"schema_version": SCHEMA_VERSION, "rationale": "x",
                "assignments": [{"robot_id": "robot_a", "tasks": [_task("a1", "kitchen")]}]}


def proposal(plan, text="here is a plan"):
    return json.dumps({"kind": "proposal", "text": text, "plan": plan})


def agreement(text="agreed"):
    return json.dumps({"kind": "agreement", "text": text})


def discussion(text="let us think"):
    return json.dumps({"kind": "discussion", "text": text})


# Parts: what each agent proposes (its own robot's tasks only).
def part(assignment, rationale="my part"):
    return {"schema_version": SCHEMA_VERSION, "rationale": rationale, "assignments": [assignment]}


A_GOES = TAKE_TURNS["assignments"][0]                               # robot_a: a1 navigate room_e
B_AFTER_A = TAKE_TURNS["assignments"][1]                            # robot_b: b1 navigate room_w after a1
B_NOW = HEAD_ON["assignments"][1]                                   # robot_b: b1 navigate room_w (no wait)
