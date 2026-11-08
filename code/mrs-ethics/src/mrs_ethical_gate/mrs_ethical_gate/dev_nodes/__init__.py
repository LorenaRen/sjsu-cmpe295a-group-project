"""Development tools of the Module 5 gate: run by hand to test the gate; the real system never
launches them (the convention of mrs_common's dev_nodes, USER_GUIDE section 5).

The fake planner is a scripted stand-in for the planner, with no LLM, for testing the gate's
REJECT -> replan loop. It is not Module 4: it makes no plan of its own, it only applies the
gate's constraints to a scenario's plan.

  fake_planner       the ROS node: ros2 run mrs_ethical_gate fake_planner, or with the gate,
                     ros2 launch mrs_ethical_gate replan_loop.launch.py
  scripted_repair    how it changes a plan for each kind of constraint
  replan_loop        when it sends, when it stops, what it records, and the fallback
  scenarios          the scenario files it starts from (scenarios/ in this package)
  run_settings       its parameters

Nothing in the gate imports this package.
"""
