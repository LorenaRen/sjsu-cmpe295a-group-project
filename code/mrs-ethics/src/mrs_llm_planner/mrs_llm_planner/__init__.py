"""Module 4: the multi-agent LLM planner.

One LLM agent per robot negotiates a TeamPlan in turns, run by a coordinator that has no LLM.
The coordinator hands the agreed plan to the gatekeeper (Module 5). No replanning in this version:
one dialogue per mission; if no plan is approved, every robot holds.

Pure-Python modules (no ROS, tested in a plain venv):
  llm_client    one interface to OpenAI, Anthropic, OpenAI-compatible servers (vLLM) and a fake
  response_log  every request and response in the run's SQLite file; replay from it
  guidelines    the plain-language rules the agents are told, keyed by the gate's rule ids
  prompts       the system prompt and the per-turn message
  turns         an agent's reply: parsed, validated, grounded on the map
  agent         one agent's turn: call the LLM, validate, repair a bad reply once
  parts         each agent proposes its own part; the dialogue folded into the team plan
  mission       mission goals: parse /mrs/mission, check that a plan ends each robot at its goal
  protocol      the coordinator's state machine: turn order, agreement, fallback
  settings      the nodes' parameters, checked
ROS nodes (thin shells around the above): agent_node, coordinator_node.
"""
