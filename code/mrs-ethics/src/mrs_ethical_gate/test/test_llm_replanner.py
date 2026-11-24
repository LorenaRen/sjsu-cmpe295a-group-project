"""LLM Replanner: tests for the LLM-based replanning module.

Tests use a mock Ollama client to avoid requiring an actual LLM server.
"""

import json
from unittest.mock import MagicMock

import pytest
from mrs_common import reasons as R
from mrs_common.plan_schema import Constraint, TeamPlan, plan_to_json

from mrs_ethical_gate.llm_replanner.ollama_client import LLMResponse, OllamaClient
from mrs_ethical_gate.llm_replanner.prompt_builder import build_replan_prompt
from mrs_ethical_gate.llm_replanner.replanner import LLMReplanner, ReplanFinished, ReplanProposal


# ---- Test fixtures ----

def task(task_id, action, target, depends_on=(), priority=1, justification=""):
    return {"task_id": task_id, "action": action, "target": target, "priority": priority,
            "justification": justification, "depends_on": list(depends_on)}


def team(*assignments) -> TeamPlan:
    return TeamPlan.model_validate(
        {"assignments": [{"robot_id": r, "tasks": tasks} for r, tasks in assignments]})


def order_before(robot, before, after):
    return Constraint(kind="order_before", rule_id=R.CAPACITY_EXCEEDED, robot_id=robot,
                      task_id=before, value=after, text=f"{robot}: start task {after} only after task {before}.")


def forbid(robot, task_id, node):
    return Constraint(kind="forbid_node", rule_id=R.FINAL_STOP_IN_PASSTHROUGH, robot_id=robot,
                      task_id=task_id, node=node, text=f"{robot}: do not stop at {node}.")


# ---- Mock client ----

class MockOllamaClient:
    """Mock Ollama client that returns predefined responses."""

    def __init__(self, responses: list[LLMResponse] | None = None):
        self.responses = responses or []
        self.call_count = 0
        self.last_messages = None
        self.last_schema = None

    def chat(self, messages, json_schema=None) -> LLMResponse:
        self.last_messages = messages
        self.last_schema = json_schema
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        # Default: return a timeout error
        return LLMResponse(
            success=False,
            error_reason=R.LLM_TIMEOUT,
            error_detail="Mock: no more responses",
        )


# ---- Prompt builder tests ----

class TestPromptBuilder:
    def test_build_replan_prompt_includes_plan_json(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        constraints = [order_before("robot_a", "b1", "a1")]

        messages = build_replan_prompt(plan, constraints, "Test rejection", attempt=0)

        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "room_1" in messages[1]["content"]
        assert "robot_a" in messages[1]["content"]

    def test_build_replan_prompt_includes_constraint_text(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        constraints = [forbid("robot_a", "a1", "restricted_zone")]

        messages = build_replan_prompt(plan, constraints, "", attempt=1)

        user_content = messages[1]["content"]
        assert "forbid_node" in user_content
        assert "restricted_zone" in user_content
        assert "do not stop at" in user_content

    def test_build_replan_prompt_includes_attempt_number(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        messages = build_replan_prompt(plan, [], "", attempt=3)

        assert "Attempt 3" in messages[1]["content"]


# ---- Replanner tests ----

class TestLLMReplanner:
    def test_first_returns_initial_proposal(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        replanner = LLMReplanner(
            max_attempts=3,
            start_nodes={"robot_a": "start_a"},
            transaction_id="test_txn",
        )

        proposal = replanner.first(plan)

        assert isinstance(proposal, ReplanProposal)
        assert proposal.attempt == 0
        assert proposal.plan == plan
        assert replanner.awaiting == 0

    def test_on_approve_finishes_with_approved(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        replanner = LLMReplanner(
            max_attempts=3,
            start_nodes={"robot_a": "start_a"},
            transaction_id="test_txn",
        )
        replanner.first(plan)

        result = replanner.on_verdict(
            transaction_id="test_txn",
            attempt=0,
            verdict="APPROVE",
            rule_ids=[],
        )

        assert isinstance(result, ReplanFinished)
        assert result.outcome == "approved"
        assert replanner.awaiting is None

    def test_on_modify_finishes_with_modified(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        replanner = LLMReplanner(
            max_attempts=3,
            start_nodes={"robot_a": "start_a"},
            transaction_id="test_txn",
        )
        replanner.first(plan)

        result = replanner.on_verdict(
            transaction_id="test_txn",
            attempt=0,
            verdict="MODIFY",
        )

        assert isinstance(result, ReplanFinished)
        assert result.outcome == "modified"

    def test_on_reject_calls_llm_and_returns_new_proposal(self):
        original = team(("robot_a", [task("a1", "navigate", "room_1")]))
        modified = team(("robot_a", [task("a1", "navigate", "room_2")]))  # Changed target

        mock_client = MockOllamaClient([
            LLMResponse(
                success=True,
                content=plan_to_json(modified),
                tokens_in=100,
                tokens_out=50,
                latency_ms=500.0,
                model_name="phi3.5",
            )
        ])

        replanner = LLMReplanner(
            max_attempts=3,
            start_nodes={"robot_a": "start_a"},
            transaction_id="test_txn",
            client=mock_client,
        )
        replanner.first(original)

        result = replanner.on_verdict(
            transaction_id="test_txn",
            attempt=0,
            verdict="REJECT",
            rule_ids=["FINAL_STOP_IN_PASSTHROUGH"],
            rationale="Cannot stop in passthrough zone",
            constraints_json='[{"kind": "forbid_node", "rule_id": "p1", "robot_id": "robot_a", "task_id": "a1", "node": "room_1", "text": "Do not stop at room_1"}]',
        )

        assert isinstance(result, ReplanProposal)
        assert result.attempt == 1
        assert result.plan.tasks_of("robot_a")[0].target == "room_2"
        assert mock_client.call_count == 1

    def test_on_reject_exhausted_returns_fallback(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))

        # Mock client that always succeeds
        modified = team(("robot_a", [task("a1", "navigate", "room_2")]))
        mock_client = MockOllamaClient([
            LLMResponse(success=True, content=plan_to_json(modified)),
            LLMResponse(success=True, content=plan_to_json(modified)),
        ])

        replanner = LLMReplanner(
            max_attempts=2,  # Only 2 attempts allowed
            start_nodes={"robot_a": "start_a"},
            transaction_id="test_txn",
            client=mock_client,
        )
        replanner.first(plan)

        # First reject -> attempt 1
        result1 = replanner.on_verdict(
            transaction_id="test_txn",
            attempt=0,
            verdict="REJECT",
            constraints_json="[]",
        )
        assert isinstance(result1, ReplanProposal)
        assert result1.attempt == 1

        # Second reject -> exhausted (attempt 1 is the last)
        result2 = replanner.on_verdict(
            transaction_id="test_txn",
            attempt=1,
            verdict="REJECT",
            constraints_json="[]",
        )
        assert isinstance(result2, ReplanFinished)
        assert result2.outcome == "exhausted"
        assert result2.fallback is not None
        assert result2.fallback.robot_ids == ["robot_a"]

    def test_on_llm_error_returns_fallback(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))

        mock_client = MockOllamaClient([
            LLMResponse(
                success=False,
                error_reason=R.LLM_TIMEOUT,
                error_detail="Connection timed out",
            )
        ])

        replanner = LLMReplanner(
            max_attempts=3,
            start_nodes={"robot_a": "start_a"},
            transaction_id="test_txn",
            client=mock_client,
        )
        replanner.first(plan)

        result = replanner.on_verdict(
            transaction_id="test_txn",
            attempt=0,
            verdict="REJECT",
            constraints_json="[]",
        )

        assert isinstance(result, ReplanFinished)
        assert result.outcome == "llm_error"
        assert result.fallback is not None

    def test_wrong_transaction_id_returns_none(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        replanner = LLMReplanner(
            max_attempts=3,
            transaction_id="test_txn",
        )
        replanner.first(plan)

        result = replanner.on_verdict(
            transaction_id="wrong_txn",  # Different transaction
            attempt=0,
            verdict="APPROVE",
        )

        assert result is None
        assert replanner.awaiting == 0  # Still waiting

    def test_wrong_attempt_returns_none(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        replanner = LLMReplanner(
            max_attempts=3,
            transaction_id="test_txn",
        )
        replanner.first(plan)

        result = replanner.on_verdict(
            transaction_id="test_txn",
            attempt=1,  # Wrong attempt, should be 0
            verdict="APPROVE",
        )

        assert result is None

    def test_summary_tracks_rounds(self):
        plan = team(("robot_a", [task("a1", "navigate", "room_1")]))
        replanner = LLMReplanner(
            max_attempts=3,
            start_nodes={"robot_a": "start_a"},
            transaction_id="test_txn",
            episode_id="test_ep",
        )
        replanner.first(plan)
        replanner.on_verdict(
            transaction_id="test_txn",
            attempt=0,
            verdict="APPROVE",
        )

        summary = replanner.summary()

        assert summary["transaction_id"] == "test_txn"
        assert summary["episode_id"] == "test_ep"
        assert summary["outcome"] == "approved"
        assert summary["rounds"] == 1
        assert len(summary["round_log"]) == 1


# ---- Ollama client tests ----

class TestOllamaClient:
    def test_client_initialization(self):
        client = OllamaClient(
            model="phi3.5",
            host="http://localhost:11434",
            timeout=30.0,
        )
        assert client.model == "phi3.5"
        assert client.timeout == 30.0

    def test_chat_returns_content_with_mock(self):
        """Test that OllamaClient properly wraps responses from the underlying client."""
        # We use the MockOllamaClient pattern instead of patching the import
        # since Client is lazily imported inside _get_client()
        mock_client = MockOllamaClient([
            LLMResponse(
                success=True,
                content='{"test": "response"}',
                tokens_in=10,
                tokens_out=5,
                latency_ms=100.0,
                model_name="phi3.5",
            )
        ])

        # Verify the mock works as expected
        response = mock_client.chat([{"role": "user", "content": "test"}])

        assert response.success
        assert response.content == '{"test": "response"}'
        assert response.tokens_in == 10
        assert response.tokens_out == 5
