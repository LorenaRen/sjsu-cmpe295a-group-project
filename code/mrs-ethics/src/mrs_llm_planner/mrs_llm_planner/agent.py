"""One robot's planning agent: one turn = one LLM call, plus up to max_repairs repairs (Module 4). Pure Python.

  agent = Agent(context, client, temperature=0.7)
  result = agent.take_turn(TurnRequest(round=1, transcript=(...)))
  if result.ok: publish result.reply  (proposal, agreement or discussion)
  else:         publish failure_text(result) as a discussion: the robot skips its turn

The agent proposes only its own robot's part (parts.py). With `constrained` (default), every call
carries turns.reply_schema(): backends that enforce a schema while generating cannot return
malformed JSON, another robot's id, an unknown node or a bad task id. What a schema cannot
express (the goal, cycles, clashes with teammates' parts) is checked by parse_reply(); a reply
that fails is sent back with the error, at repair_temperature (lower: the repair should be careful,
not creative), up to max_repairs times ("Structured output" in Module 4).

An agent is never silent: whatever goes wrong (llm_malformed after the repairs, llm_timeout,
llm_error), failure_text() says so in a discussion turn and the dialogue goes on. Only a crashed
node says nothing, and the coordinator's turn deadline catches that.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from mrs_common import reasons as R

from .llm_client import LLMClient, LLMError, Message
from .mission import goals_from_text
from .parts import SKIP_PREFIX, fold
from .prompts import PlannerContext, TranscriptEntry, TurnRequest, system_prompt, turn_message
from .turns import ReplyError, TurnReply, parse_reply, reply_schema

COORDINATOR = "coordinator"   # DialogueTurn.COORDINATOR


@dataclass(frozen=True)
class AgentTurnResult:
    ok: bool
    reply: Optional[TurnReply] = None
    reason: str = ""            # R.LLM_TIMEOUT, R.LLM_ERROR or R.LLM_MALFORMED when not ok
    error: str = ""             # the last error message, for the log
    calls: int = 0              # LLM calls made for this turn (1 + repairs)
    repaired: bool = False      # the first reply was bad and the second one was used
    latency_ms: float = 0.0     # wall time of the whole turn
    tokens_in: int = 0
    tokens_out: int = 0


class Agent:
    def __init__(self, context: PlannerContext, client: LLMClient, *, temperature: float = 0.7,
                 constrained: bool = True, max_repairs: int = 1, repair_temperature: float = 0.2):
        self.context = context
        self.client = client
        self.temperature = temperature
        self.constrained = constrained
        self.max_repairs = max_repairs
        self.repair_temperature = repair_temperature
        self.schema_off_reason = ""   # set when the backend refused a schema: constrained output is then off
        self.system = system_prompt(context)
        self._robots = tuple(context.team)
        self._nodes = tuple(context.map_dict.get("nodes", {}))

    @property
    def robot_id(self) -> str:
        return self.context.robot_id

    def take_turn(self, request: TurnRequest) -> AgentTurnResult:
        t0 = time.monotonic()
        messages = [Message("system", self.system), Message("user", turn_message(request, self.context))]
        others = fold(request.transcript, self._robots).parts
        schema = None
        if self.constrained:
            teammate_ids = [t["task_id"] for r, p in others.items() if r != self.robot_id
                            for t in p.get("tasks", []) if isinstance(t, dict) and t.get("task_id")]
            schema = reply_schema(self.robot_id, self._nodes, teammate_ids)
        calls = tokens_in = tokens_out = 0
        last_error = ""

        def done(**kw) -> AgentTurnResult:
            return AgentTurnResult(calls=calls, tokens_in=tokens_in, tokens_out=tokens_out,
                                   latency_ms=(time.monotonic() - t0) * 1000.0, **kw)

        for try_no in range(1 + self.max_repairs):
            temperature = self.temperature if try_no == 0 else self.repair_temperature
            try:
                completion = self.client.complete(messages, temperature=temperature, json_mode=True, schema=schema)
            except LLMError as e:
                calls += 1
                if schema is None or e.reason != R.LLM_ERROR or not str(e).startswith("HTTP 4"):
                    return done(ok=False, reason=e.reason, error=str(e))
                # the request was refused (HTTP 4xx): the backend may not support this schema (an older
                # Ollama). Go on without it, for this and every later turn.
                self.constrained, self.schema_off_reason, schema = False, str(e), None
                try:
                    completion = self.client.complete(messages, temperature=temperature, json_mode=True)
                except LLMError as e2:
                    calls += 1
                    return done(ok=False, reason=e2.reason, error=str(e2))
            calls += 1
            tokens_in += completion.tokens_in
            tokens_out += completion.tokens_out
            try:
                reply = parse_reply(completion.text, robot_id=self.robot_id, team=self._robots,
                                    graph_nodes=self._nodes, others=others, goals=request.goals)
            except ReplyError as e:
                last_error = str(e)
                messages = messages + [
                    Message("assistant", completion.text),
                    Message("user", f"Your reply cannot be used: {last_error}. Send the corrected reply: "
                                    "exactly one JSON object in the reply format."),
                ]
                continue
            return done(ok=True, reply=reply, repaired=try_no > 0)
        return done(ok=False, reason=R.LLM_MALFORMED, error=last_error)




def failure_text(result_or_reason, error: str = "") -> str:
    """What an agent says, as a discussion turn, when its turn failed: teammates see why, and the
    coordinator moves on to the next robot. Takes an AgentTurnResult, or a reason and an error."""
    if isinstance(result_or_reason, AgentTurnResult):
        reason, error = result_or_reason.reason, result_or_reason.error
    else:
        reason = result_or_reason
    why = {R.LLM_MALFORMED: "I could not produce a valid plan",
           R.LLM_TIMEOUT: "my language model did not answer in time",
           R.LLM_ERROR: "my language model failed"}.get(reason, "something went wrong on my side")
    return f"{SKIP_PREFIX}: {why}. Problem: {error[:300]}"


class TranscriptTracker:
    """What an agent node has heard on /mrs/dialogue for the current mission.

    Every agent hears every turn. The tracker keeps the turns of the latest transaction_id: the
    coordinator's opening prompt (mission, goals, where the robots are) and every agent turn. The
    short "your turn" prompts are left out of the transcript; they only say who is next.
    """

    def __init__(self):
        self.transaction_id = ""
        self.entries: list[TranscriptEntry] = []
        self.answered: set[tuple[str, int]] = set()   # (transaction_id, round) answered

    def hear(self, *, transaction_id: str, round: int, kind: str, speaker_id: str,
             text: str = "", plan_json: str = "") -> None:
        if transaction_id != self.transaction_id:
            if speaker_id != COORDINATOR or kind != "prompt":
                return   # a late turn of an older mission, or we joined mid-dialogue
            self.transaction_id, self.entries = transaction_id, [TranscriptEntry(COORDINATOR, "prompt", text)]
            return
        if kind == "prompt":
            return
        self.entries.append(TranscriptEntry(speaker_id, kind, text, plan_json))

    def request(self, *, round: int, world_summary: Optional[dict] = None,
                notes: tuple[str, ...] = ()) -> TurnRequest:
        goals = goals_from_text(self.entries[0].text) if self.entries else {}
        return TurnRequest(round=round, transcript=tuple(self.entries),
                           world_summary=world_summary, notes=notes, goals=goals)
