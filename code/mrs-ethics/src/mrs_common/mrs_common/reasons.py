"""Reason codes (Module 1): one vocabulary for why something fell back, failed or was overridden.

Pure Python, no ROS imports. Use these strings, never free text, wherever a reason is recorded:

  EpisodeEvent.detail_json   {"reason": <code>, ...}   build it with detail(<code>, ...)
  TaskStatus.detail          the code, for STATE_FAILED and STATE_TIMED_OUT
  hold_all_plan(reason=...)  the code that caused the fallback

so the analysis (Module 11) can count each kind of failure, and results stay comparable with the
previous team's single-robot study, which used the same names for llm_timeout, llm_malformed and
safety_override. Add a code here (and a line in the user guide) before using a new one.
"""
from __future__ import annotations

from typing import Any

# ---- planner (Module 4) ------------------------------------------------------
LLM_TIMEOUT = "llm_timeout"                # an LLM call exceeded its wall-clock timeout
LLM_ERROR = "llm_error"                    # API error that retries did not fix (rate limit, network, 5xx)
LLM_MALFORMED = "llm_malformed"            # output still invalid after the one retry with the validation error
PLANNING_EXHAUSTED = "planning_exhausted"  # attempt cap reached without an approved plan; all robots hold

# ---- executor and navigation (Module 6) ---------------------------------------
EDGE_BLOCKED = "edge_blocked"              # an edge on the robot's path is blocked; executor reroutes or holds
NO_ROUTE = "no_route"                      # no path to the target on the graph without blocked edges
ROBOT_STUCK = "robot_stuck"                # robot has not progressed for the stuck time (e.g. CBF deadlock)
TASK_TIMEOUT = "task_timeout"              # task not done within its time limit

# ---- safety filter (Module 6) -------------------------------------------------
SAFETY_OVERRIDE = "safety_override"        # CBF changed the nominal command
QP_INFEASIBLE = "qp_infeasible"            # CBF QP had no solution; zero velocity for that cycle

# ---- runner (Module 9) --------------------------------------------------------
EPISODE_TIMEOUT = "episode_timeout"        # mission not finished within the scenario time limit

ALL: frozenset[str] = frozenset(v for k, v in dict(globals()).items() if k.isupper() and isinstance(v, str))


def is_reason(code: str) -> bool:
    return code in ALL


def check(code: str) -> str:
    """Return code unchanged, or raise ValueError if it is not a known reason code."""
    if code not in ALL:
        raise ValueError(f"unknown reason code '{code}'; known: {sorted(ALL)}")
    return code


def detail(code: str, **extra: Any) -> dict[str, Any]:
    """EpisodeEvent detail dict with a checked reason code: detail(EDGE_BLOCKED, edge=..., robot=...)."""
    return {"reason": check(code), **extra}
