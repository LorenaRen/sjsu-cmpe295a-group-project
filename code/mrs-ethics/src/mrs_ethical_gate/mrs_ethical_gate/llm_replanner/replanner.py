"""LLM-based replanner for multi-robot coordination.

Replaces the scripted FakePlanner (dev_nodes/scripted_repair.py) with an actual LLM
that can understand constraint feedback and generate modified plans.

The flow:
    1. Receive REJECT verdict with constraints
    2. Build prompt with original plan + constraint texts
    3. Call LLM (Phi-3.5-mini via Ollama) to generate modified plan
    4. Parse and validate the response as TeamPlan
    5. Return the new plan or error

This module is pure Python (no ROS) for testability.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from mrs_common import reasons as R
from mrs_common.plan_schema import (
    Constraint,
    TeamPlan,
    constraints_from_json,
    hold_all_plan,
    plan_from_json,
    team_plan_json_schema,
)
from pydantic import ValidationError

from .ollama_client import LLMResponse, OllamaClient
from .prompt_builder import build_replan_prompt


@dataclass(frozen=True)
class ReplanResult:
    """Result of an LLM replan attempt."""
    success: bool
    plan: TeamPlan | None = None          # The new plan if successful
    error_reason: str | None = None       # One of reasons.LLM_* if failed
    error_detail: str | None = None       # Human-readable error message
    changes: tuple[str, ...] = ()         # Description of what changed
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    model_name: str = ""


@dataclass(frozen=True)
class ReplanProposal:
    """A plan to send as the next attempt (mirrors dev_nodes/replan_loop.Proposal)."""
    attempt: int
    plan: TeamPlan
    changes: tuple[str, ...]
    planning_ms: float
    tokens_in: int = 0
    tokens_out: int = 0
    model_name: str = ""

    @property
    def digest(self) -> str:
        """ProposedPlan.dialogue_digest: what made this attempt differ from the one before."""
        return "LLM replanner: " + ("; ".join(self.changes) if self.changes else "modified plan")


@dataclass(frozen=True)
class ReplanFinished:
    """The replan loop has ended (mirrors dev_nodes/replan_loop.Finished)."""
    outcome: str                               # approved, modified, stuck, exhausted, llm_error
    fallback: TeamPlan | None = None           # The fallback plan if any
    fallback_attempt: int | None = None
    event_detail: dict[str, Any] | None = None


class LLMReplanner:
    """LLM-based replanner that handles REJECT -> replan cycles.

    Usage:
        replanner = LLMReplanner(max_attempts=3, start_nodes={"robot_a": "node_1", ...})

        # Initial plan (from Mu's basic planner)
        proposal = replanner.first(initial_plan)

        # On each verdict from the checker
        result = replanner.on_verdict(verdict)
        if isinstance(result, ReplanProposal):
            # Send the new plan to the checker
            ...
        elif isinstance(result, ReplanFinished):
            # Done (approved, stuck, or exhausted)
            ...
    """

    def __init__(
        self,
        max_attempts: int = 3,
        start_nodes: Mapping[str, str] | None = None,
        transaction_id: str = "",
        episode_id: str = "",
        client: OllamaClient | None = None,
    ):
        if max_attempts < 1:
            raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

        self.max_attempts = max_attempts
        self.transaction_id = transaction_id
        self.episode_id = episode_id
        self._start_nodes = dict(start_nodes or {})
        self._client = client or OllamaClient()

        # State
        self._sent: ReplanProposal | None = None
        self._finished: ReplanFinished | None = None
        self._rounds: list[dict[str, Any]] = []
        self._original_plan: TeamPlan | None = None

    @property
    def awaiting(self) -> int | None:
        """The attempt whose verdict the loop waits for, or None."""
        return self._sent.attempt if self._sent is not None and self._finished is None else None

    @property
    def finished(self) -> ReplanFinished | None:
        return self._finished

    @property
    def rounds(self) -> list[dict[str, Any]]:
        """One record for each verdict received."""
        return self._rounds

    def first(self, plan: TeamPlan) -> ReplanProposal:
        """Start the replan loop with an initial plan."""
        if self._sent is not None:
            raise RuntimeError("first() was called already")

        self._original_plan = plan
        self._sent = ReplanProposal(
            attempt=0,
            plan=plan,
            changes=(),
            planning_ms=0.0,
        )
        return self._sent

    def on_verdict(
        self,
        transaction_id: str,
        attempt: int,
        verdict: str,
        rule_ids: Sequence[str] = (),
        risk_score: float = 0.0,
        rationale: str = "",
        constraints_json: str = "[]",
        latency_ms: float = 0.0,
    ) -> ReplanProposal | ReplanFinished | None:
        """Process a verdict from the checker.

        Returns:
            - ReplanProposal: The next plan to send
            - ReplanFinished: The loop has ended
            - None: This verdict is not for the current attempt
        """
        # Check if this is the verdict we're waiting for
        if self.awaiting is None or (transaction_id, attempt) != (self.transaction_id, self.awaiting):
            return None

        if verdict not in ("APPROVE", "MODIFY", "REJECT"):
            raise ValueError(f"unknown verdict {verdict!r}")

        # Record this round
        record: dict[str, Any] = {
            "round": attempt + 1,
            "attempt": attempt,
            "verdict": verdict,
            "rule_ids": list(rule_ids),
            "risk_score": risk_score,
            "gate_latency_ms": latency_ms,
            "rationale": rationale,
        }

        # Handle APPROVE or MODIFY
        if verdict != "REJECT":
            outcome = "approved" if verdict == "APPROVE" else "modified"
            self._rounds.append({**record, "then": outcome})
            return self._finish(ReplanFinished(outcome))

        # Handle REJECT - need to replan
        constraints = constraints_from_json(constraints_json)

        # Check if we've exhausted attempts
        if attempt + 1 >= self.max_attempts:
            self._rounds.append({
                **record,
                "constraints_count": len(constraints),
                "then": "exhausted: no attempt left; FALLBACK",
            })
            return self._finish(self._fallback(attempt, exhausted=True))

        # Try to replan with LLM
        start_time = time.perf_counter()
        result = self._replan_with_llm(self._sent.plan, constraints, rationale, attempt)
        planning_ms = (time.perf_counter() - start_time) * 1000

        if not result.success:
            # LLM failed - fall back
            self._rounds.append({
                **record,
                "constraints_count": len(constraints),
                "llm_error": result.error_reason,
                "llm_error_detail": result.error_detail,
                "then": f"LLM error: {result.error_reason}; FALLBACK",
            })
            return self._finish(self._fallback(attempt, llm_error=result.error_reason))

        # Success - send the new plan
        self._rounds.append({
            **record,
            "constraints_count": len(constraints),
            "llm_latency_ms": result.latency_ms,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "then": f"attempt {attempt + 1} sent",
        })

        proposal = ReplanProposal(
            attempt=attempt + 1,
            plan=result.plan,
            changes=result.changes,
            planning_ms=planning_ms,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            model_name=result.model_name,
        )
        self._sent = proposal
        return proposal

    def _replan_with_llm(
        self,
        plan: TeamPlan,
        constraints: Sequence[Constraint],
        rationale: str,
        attempt: int,
    ) -> ReplanResult:
        """Call the LLM to generate a modified plan."""
        # Build the prompt
        messages = build_replan_prompt(plan, constraints, rationale, attempt)

        # Get the TeamPlan JSON schema for structured output
        schema = team_plan_json_schema()

        # Call the LLM
        response = self._client.chat(messages, json_schema=schema)

        if not response.success:
            return ReplanResult(
                success=False,
                error_reason=response.error_reason,
                error_detail=response.error_detail,
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
                model_name=response.model_name,
            )

        # Parse the response as TeamPlan
        try:
            new_plan = plan_from_json(response.content)
        except (json.JSONDecodeError, ValidationError) as e:
            return ReplanResult(
                success=False,
                error_reason=R.LLM_MALFORMED,
                error_detail=f"Invalid plan JSON: {e}",
                tokens_in=response.tokens_in,
                tokens_out=response.tokens_out,
                latency_ms=response.latency_ms,
                model_name=response.model_name,
            )

        # Describe what changed
        changes = self._describe_changes(plan, new_plan)

        return ReplanResult(
            success=True,
            plan=new_plan,
            changes=changes,
            tokens_in=response.tokens_in,
            tokens_out=response.tokens_out,
            latency_ms=response.latency_ms,
            model_name=response.model_name,
        )

    def _describe_changes(self, old: TeamPlan, new: TeamPlan) -> tuple[str, ...]:
        """Describe what changed between two plans."""
        changes = []

        old_tasks = {t.task_id: t for a in old.assignments for t in a.tasks}
        new_tasks = {t.task_id: t for a in new.assignments for t in a.tasks}

        # Check for target changes
        for task_id, new_task in new_tasks.items():
            if task_id in old_tasks:
                old_task = old_tasks[task_id]
                if new_task.target != old_task.target:
                    changes.append(f"{task_id}.target: {old_task.target} -> {new_task.target}")
                new_deps = set(new_task.depends_on) - set(old_task.depends_on)
                if new_deps:
                    changes.append(f"{task_id}.depends_on += {', '.join(new_deps)}")
            else:
                changes.append(f"added task {task_id}")

        # Check for removed tasks
        for task_id in old_tasks:
            if task_id not in new_tasks:
                changes.append(f"removed task {task_id}")

        return tuple(changes) if changes else ("plan modified",)

    def _finish(self, finished: ReplanFinished) -> ReplanFinished:
        """Mark the loop as finished."""
        self._finished = finished
        return finished

    def _fallback(
        self,
        attempt: int,
        *,
        exhausted: bool = False,
        llm_error: str | None = None,
    ) -> ReplanFinished:
        """Create a fallback plan (all robots hold position)."""
        if not self._start_nodes:
            # No start nodes known - can't create fallback
            outcome = "exhausted" if exhausted else "llm_error"
            return ReplanFinished(
                outcome=outcome,
                event_detail={
                    "reason": R.PLANNING_EXHAUSTED,
                    "error": llm_error or "no start nodes for fallback",
                },
            )

        fleet = list(self._start_nodes.keys())
        detail = R.detail(
            R.PLANNING_EXHAUSTED,
            transaction_id=self.transaction_id,
            attempts=attempt + 1,
            max_attempts=self.max_attempts,
        )
        if llm_error:
            detail["llm_error"] = llm_error

        outcome = "exhausted" if exhausted else "llm_error"
        return ReplanFinished(
            outcome=outcome,
            fallback=hold_all_plan(fleet, self._start_nodes, R.PLANNING_EXHAUSTED),
            fallback_attempt=attempt,
            event_detail=detail,
        )

    def summary(self) -> dict[str, Any]:
        """Summary of the replan session for logging/analysis."""
        f = self._finished
        total_tokens_in = sum(r.get("tokens_in", 0) for r in self._rounds)
        total_tokens_out = sum(r.get("tokens_out", 0) for r in self._rounds)

        return {
            "transaction_id": self.transaction_id,
            "episode_id": self.episode_id,
            "max_attempts": self.max_attempts,
            "outcome": f.outcome if f else None,
            "rounds": len(self._rounds),
            "total_tokens_in": total_tokens_in,
            "total_tokens_out": total_tokens_out,
            "round_log": self._rounds,
            "fallback": f.fallback is not None if f else False,
        }
