#!/usr/bin/env python3
"""Test replan with a real Ollama LLM.

Usage:
  1. Start Ollama: ollama serve
  2. Pull model: ollama pull phi3.5
  3. Run this script: python scripts/test_replan_live.py

This script tests the full replan flow:
  - Create a plan that will be REJECTED (head-on collision)
  - LLMReplanner calls the real LLM to fix it
  - Check if the new plan is valid
"""

import json
import sys
import time
from pathlib import Path

# Add src directories to path
src_root = Path(__file__).parent.parent / "src"
for pkg in ["mrs_common", "mrs_ethical_gate", "mrs_llm_planner", "fleet_checker", "mrs_sim"]:
    sys.path.insert(0, str(src_root / pkg))

from mrs_common.plan_schema import Constraint, TeamPlan, plan_to_json
from mrs_ethical_gate.llm_replanner import LLMReplanner
from mrs_ethical_gate.llm_replanner.ollama_client import OllamaClient


def make_bad_plan() -> TeamPlan:
    """A plan where both robots go at once -> will cause collision."""
    return TeamPlan.model_validate({
        "assignments": [
            {"robot_id": "robot_a", "tasks": [
                {"task_id": "a1", "action": "navigate", "target": "room_e",
                 "priority": 1, "justification": "mission", "depends_on": []}
            ]},
            {"robot_id": "robot_b", "tasks": [
                {"task_id": "b1", "action": "navigate", "target": "room_w",
                 "priority": 1, "justification": "mission", "depends_on": []}
            ]}
        ]
    })


def make_constraints() -> list[Constraint]:
    """Constraints from a REJECT: robot_b must wait for robot_a."""
    return [
        Constraint(
            kind="order_before",
            rule_id="capacity_exceeded",
            robot_id="robot_b",
            task_id="a1",
            value="b1",
            text="robot_b: start task b1 only after task a1 finishes."
        )
    ]


def test_replan_with_ollama():
    print("=" * 60)
    print("Testing LLM Replanner with Ollama")
    print("=" * 60)

    # Check Ollama connection
    print("\n1. Checking Ollama connection...")
    try:
        client = OllamaClient(
            model="phi3.5",
            host="http://localhost:11434",
            timeout=60.0,
        )
        # Quick test
        response = client.chat([{"role": "user", "content": "Say 'OK' if you can hear me."}])
        if not response.success:
            print(f"   ERROR: {response.error_reason}: {response.error_detail}")
            return False
        print(f"   OK - Model responded: {response.content[:50]}...")
    except Exception as e:
        print(f"   ERROR: Cannot connect to Ollama: {e}")
        print("   Make sure Ollama is running: ollama serve")
        return False

    # Create bad plan and constraints
    print("\n2. Creating test scenario...")
    bad_plan = make_bad_plan()
    constraints = make_constraints()
    print(f"   Bad plan: both robots go at once (no dependencies)")
    print(f"   Constraint: robot_b must wait for a1")

    # Create replanner
    print("\n3. Calling LLM to fix the plan...")
    replanner = LLMReplanner(
        max_attempts=3,
        start_nodes={"robot_a": "room_w_s", "robot_b": "room_e_n"},
        transaction_id="test-live",
        episode_id="test-episode",
        client=client,
    )

    start_time = time.time()
    result = replanner._replan_with_llm(bad_plan, constraints, "Robots will collide", attempt=0)
    elapsed = time.time() - start_time

    print(f"   Latency: {elapsed:.2f}s")
    print(f"   Tokens in: {result.tokens_in}, out: {result.tokens_out}")

    if not result.success:
        print(f"   ERROR: Replan failed: {result.error_reason}")
        print(f"   Detail: {result.error_detail}")
        return False

    # Check the new plan
    print("\n4. Checking the new plan...")
    new_plan = result.plan
    print(f"   New plan JSON:\n{plan_to_json(new_plan)}")

    # Check if b1 now depends on a1
    b_tasks = new_plan.tasks_of("robot_b")
    if b_tasks and "a1" in b_tasks[0].depends_on:
        print("\n   ✓ SUCCESS: robot_b's task now depends on a1")
        return True
    else:
        print("\n   ✗ FAIL: robot_b's task still doesn't depend on a1")
        print(f"   b1.depends_on = {b_tasks[0].depends_on if b_tasks else 'N/A'}")
        return False


def main():
    success = test_replan_with_ollama()
    print("\n" + "=" * 60)
    if success:
        print("TEST PASSED ✓")
    else:
        print("TEST FAILED ✗")
    print("=" * 60)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
