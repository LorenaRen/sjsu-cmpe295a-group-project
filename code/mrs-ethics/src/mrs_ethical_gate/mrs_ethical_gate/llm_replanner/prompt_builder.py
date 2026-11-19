"""Prompt construction for LLM-based replanning.

Builds prompts that include:
- The original plan (as JSON)
- The rejection reason and constraints (human-readable text)
- Instructions for generating a valid modified plan
"""

from __future__ import annotations

from typing import Sequence

from mrs_common.plan_schema import Constraint, TeamPlan, plan_to_json

# System prompt for the replanner LLM
SYSTEM_PROMPT = """You are a multi-robot coordination planner. Your task is to modify robot movement plans to satisfy safety and policy constraints.

You will receive:
1. A rejected plan in JSON format
2. A list of constraints explaining why the plan was rejected and what must change

Your response must be a valid JSON object matching the TeamPlan schema. The plan must:
- Keep the same robots (robot_ids)
- Modify tasks to satisfy all constraints
- Use only valid actions: "navigate", "wait", "hold_position"
- Ensure no circular dependencies in depends_on
- Keep task_ids unique

Common constraint types:
- order_before: Task A must complete before Task B starts. Add A to B's depends_on.
- forbid_node: A robot cannot stop/wait at a specific node. Change the task's target.
- avoid_node: A robot should not go to a specific node. Retarget tasks going there.
- custom: A text explanation of what needs to change.

Output only the JSON plan, no explanation."""


def build_replan_prompt(
    plan: TeamPlan,
    constraints: Sequence[Constraint],
    rationale: str = "",
    attempt: int = 0,
) -> list[dict[str, str]]:
    """Build the messages for a replan request.

    Args:
        plan: The rejected TeamPlan
        constraints: List of Constraint objects from the REJECT verdict
        rationale: The checker's human-readable explanation
        attempt: Current attempt number (for context)

    Returns:
        List of message dicts for the LLM chat API
    """
    # Build constraint descriptions
    constraint_texts = []
    for i, c in enumerate(constraints, 1):
        parts = [f"{i}. [{c.kind}]"]
        if c.robot_id:
            parts.append(f"Robot: {c.robot_id}")
        if c.task_id:
            parts.append(f"Task: {c.task_id}")
        if c.node:
            parts.append(f"Node: {c.node}")
        if c.value:
            parts.append(f"Value: {c.value}")
        parts.append(f"Rule: {c.rule_id}")
        parts.append(f"Description: {c.text}")
        constraint_texts.append(" | ".join(parts))

    constraints_section = "\n".join(constraint_texts) if constraint_texts else "No specific constraints provided."

    # Build the user message
    user_content = f"""## Rejected Plan (Attempt {attempt})

```json
{plan_to_json(plan)}
```

## Rejection Reason

{rationale if rationale else "The plan violates safety or policy rules."}

## Constraints to Satisfy

{constraints_section}

## Your Task

Modify the plan above to satisfy ALL constraints. Output only the valid JSON plan."""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def build_initial_plan_prompt(
    mission: str,
    robot_ids: Sequence[str],
    graph_nodes: Sequence[str],
    zones: Sequence[dict] | None = None,
) -> list[dict[str, str]]:
    """Build prompt for initial plan generation (for Mu's basic planner).

    This is a placeholder - Mu will implement the actual initial planning logic.
    The replanner only handles REJECT -> replan cycles.

    Args:
        mission: Natural language description of the mission
        robot_ids: Available robots
        graph_nodes: Valid target nodes
        zones: Optional zone information (restricted areas, etc.)

    Returns:
        List of message dicts for the LLM chat API
    """
    nodes_str = ", ".join(graph_nodes[:20])  # Limit to avoid token overflow
    if len(graph_nodes) > 20:
        nodes_str += f", ... ({len(graph_nodes)} total)"

    robots_str = ", ".join(robot_ids)

    zones_section = ""
    if zones:
        zone_lines = [f"- {z.get('id', '?')}: {z.get('kind', '?')}" for z in zones[:10]]
        zones_section = "\n## Zones\n" + "\n".join(zone_lines)

    user_content = f"""## Mission

{mission}

## Available Robots

{robots_str}

## Valid Target Nodes

{nodes_str}
{zones_section}

## Your Task

Create a TeamPlan that accomplishes the mission. Output only valid JSON."""

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
