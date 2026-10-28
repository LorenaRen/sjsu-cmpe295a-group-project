"""Development nodes: run by hand while building and debugging. The real system never launches them.

  sample_plan_pub   stands in for the LLM planner (Module 4): publishes a fixed sample plan
  plan_echo         inspects /mrs/proposed_plan and says whether each plan is valid

Recording for audit and analysis is NOT done here; that is Module 7 (mrs_telemetry).
Later modules can add their own dev nodes here (e.g. a fake world-state publisher).
"""
