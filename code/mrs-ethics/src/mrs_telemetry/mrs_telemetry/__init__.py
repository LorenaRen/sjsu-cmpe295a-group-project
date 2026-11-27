"""Module 7: recording. One self-describing run folder per episode (MCAP bag, Gazebo state log,
events.jsonl, run.json, llm_log.sqlite).

Pure-Python modules (no ROS): topics, run_folder, events, check_run.
ROS node: telemetry_node (writes events.jsonl).
"""
