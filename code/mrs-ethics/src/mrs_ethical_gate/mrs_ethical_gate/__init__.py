"""Module 5 gate: every ProposedPlan is checked by the shared checker (fleet_checker).

Pure Python, testable without ROS: settings (parameters + policy file), starts (where each
robot starts), session (one plan in, one decision out, and the fairness ledger). ROS only in
gate_node, which receives, calls GateSession.decide and publishes.
"""
