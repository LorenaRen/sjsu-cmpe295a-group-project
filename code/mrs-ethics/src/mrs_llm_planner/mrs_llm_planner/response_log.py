"""Every LLM request and response, in the run's SQLite file (Module 4, step 2). Pure Python.

  log = ResponseLog("results/exp1/run1/llm_cache.sqlite", run_id="run1")
  client = LoggingClient(make_client(config), log, agent="robot_a")
  client.context = CallContext(episode_id=..., transaction_id=..., attempt=0, round=1)
  client.complete(...)                       # recorded, with the context above

  replay = ReplayClient(path, run_id="run1", agent="robot_a")   # same answers, in the same order

The log is the record of exactly what each agent saw and answered, keyed by run and call number,
so no message needs a field for it. Replaying a run reads its own answers back: debugging is exact
and free. Never replay one run's answers into another run of an experiment: identical prompts
would give identical answers, and 20 seeds would become 20 copies of one sample.

Several agents of one run (separate processes) may write one file: SQLite serialises the writes;
each record is one short transaction.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Sequence, Union

from .llm_client import Completion, LLMClient, LLMError, LLMTimeout, Message

_SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    agent          TEXT NOT NULL,
    call_no        INTEGER NOT NULL,     -- per (run, agent), from 1
    episode_id     TEXT,
    transaction_id TEXT,
    attempt        INTEGER,
    round          INTEGER,
    model          TEXT,
    temperature    REAL,
    request_json   TEXT NOT NULL,        -- the messages, as [{"role", "content"}]
    response_text  TEXT,                 -- NULL when the call failed
    error          TEXT,                 -- reason code and message when it failed
    latency_ms     REAL,
    tokens_in      INTEGER,
    tokens_out     INTEGER,
    wall_time      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS calls_by_agent ON calls (run_id, agent, call_no);
CREATE TABLE IF NOT EXISTS cycles (      -- one row per planning cycle, written by the coordinator
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    episode_id     TEXT,
    transaction_id TEXT,
    mission        TEXT,
    model          TEXT,
    outcome        TEXT,                 -- approved or fallback
    cause          TEXT,                 -- fallback cause: rejected, no_agreement, cycle_timeout, ...
    detail         TEXT,
    rounds         INTEGER,
    turns          INTEGER,
    proposals      INTEGER,
    agreements     INTEGER,
    discussions    INTEGER,
    skips          INTEGER,              -- turns an agent skipped (no usable reply)
    plan_versions  INTEGER,
    duration_s     REAL,
    wall_time      REAL NOT NULL
);
"""


@dataclass
class CallContext:
    episode_id: str = ""
    transaction_id: str = ""
    attempt: int = 0
    round: int = 0


class ResponseLog:
    def __init__(self, path: Union[str, Path], run_id: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self._lock = threading.Lock()
        with self._connect() as db:
            db.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30.0)

    def record(self, *, agent: str, context: CallContext, model: str, temperature: float,
               messages: Sequence[Message], completion: Optional[Completion] = None,
               error: Optional[str] = None) -> int:
        """Store one call; returns its call number for this agent."""
        request = json.dumps([{"role": m.role, "content": m.content} for m in messages])
        with self._lock, self._connect() as db:
            (n,) = db.execute("SELECT COUNT(*) FROM calls WHERE run_id = ? AND agent = ?",
                              (self.run_id, agent)).fetchone()
            db.execute(
                "INSERT INTO calls (run_id, agent, call_no, episode_id, transaction_id, attempt, round, model,"
                " temperature, request_json, response_text, error, latency_ms, tokens_in, tokens_out, wall_time)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (self.run_id, agent, n + 1, context.episode_id, context.transaction_id, context.attempt,
                 context.round, model, temperature, request,
                 completion.text if completion else None, error,
                 completion.latency_ms if completion else None,
                 completion.tokens_in if completion else None,
                 completion.tokens_out if completion else None, time.time()))
            return n + 1

    def record_cycle(self, *, episode_id: str, transaction_id: str, mission: str, model: str,
                     stats: dict, duration_s: float) -> None:
        cols = ("outcome", "cause", "detail", "rounds", "turns", "proposals", "agreements", "discussions",
                "skips", "plan_versions")
        with self._lock, self._connect() as db:
            db.execute(f"INSERT INTO cycles (run_id, episode_id, transaction_id, mission, model, {', '.join(cols)},"
                       f" duration_s, wall_time) VALUES ({', '.join('?' * (7 + len(cols)))})",
                       (self.run_id, episode_id, transaction_id, mission, model, *[stats.get(c) for c in cols],
                        round(duration_s, 2), time.time()))

    def cycles(self) -> list[dict]:
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM cycles WHERE run_id = ? ORDER BY id", (self.run_id,))]

    def calls(self, agent: Optional[str] = None) -> list[dict]:
        """Every recorded call of this run (one agent's, if given), in order."""
        q = "SELECT * FROM calls WHERE run_id = ?" + (" AND agent = ?" if agent else "") + " ORDER BY id"
        args = (self.run_id, agent) if agent else (self.run_id,)
        with self._connect() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(q, args)]

    def totals(self) -> dict:
        """Calls, failures and tokens of this run, for results.json."""
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*), SUM(error IS NOT NULL), COALESCE(SUM(tokens_in), 0),"
                             " COALESCE(SUM(tokens_out), 0), COALESCE(SUM(latency_ms), 0)"
                             " FROM calls WHERE run_id = ?", (self.run_id,)).fetchone()
        return {"calls": row[0], "failed": row[1] or 0, "tokens_in": row[2], "tokens_out": row[3],
                "llm_ms": round(row[4], 1)}


class LoggingClient:
    """Wraps a client; records every call, failed or not, with the current `context`."""

    def __init__(self, inner: LLMClient, log: ResponseLog, agent: str):
        self.inner = inner
        self.log = log
        self.agent = agent
        self.model_name = inner.model_name
        self.context = CallContext()

    def complete(self, messages: Sequence[Message], *, temperature: float, json_mode: bool = True,
                 schema: Optional[dict] = None) -> Completion:
        try:
            completion = self.inner.complete(messages, temperature=temperature, json_mode=json_mode, schema=schema)
        except LLMError as e:
            self.log.record(agent=self.agent, context=self.context, model=self.model_name, temperature=temperature,
                            messages=messages, error=f"{e.reason}: {e}")
            raise
        self.log.record(agent=self.agent, context=self.context, model=self.model_name, temperature=temperature,
                        messages=messages, completion=completion)
        return completion


REPLANNER = "replanner"   # the `agent` of the coordinator's replan calls in the calls table


class LoggingChat:
    """Wraps a chat-style client (the replanner's OllamaClient: chat(messages, json_schema) ->
    response with success, content, error_reason, error_detail, tokens_in, tokens_out, latency_ms)
    and records every call in the response log like an agent's, so the log holds every prompt and
    answer of the run. ResponseLog is thread-safe, so this may run in a worker thread."""

    def __init__(self, inner, log: ResponseLog, *, agent: str, model: str, temperature: float,
                 context: Optional[CallContext] = None):
        self.inner = inner
        self.log = log
        self.agent = agent
        self.log_model = model   # the label in the log, e.g. ollama/phi3.5 (`model` stays the wrapped client's)
        self.temperature = temperature
        self.context = context or CallContext()

    def chat(self, messages, json_schema=None):
        response = self.inner.chat(messages, json_schema=json_schema)
        msgs = [Message(m["role"], m["content"]) for m in messages]
        if response.success:
            self.log.record(agent=self.agent, context=self.context, model=self.log_model, temperature=self.temperature,
                            messages=msgs, completion=Completion(response.content or "", self.log_model,
                                                                 response.latency_ms, response.tokens_in,
                                                                 response.tokens_out))
        else:
            self.log.record(agent=self.agent, context=self.context, model=self.log_model, temperature=self.temperature,
                            messages=msgs, error=f"{response.error_reason}: {response.error_detail}")
        return response

    def __getattr__(self, name):   # model, timeout, ... of the wrapped client
        return getattr(self.inner, name)


class ReplayClient:
    """Answers an agent's calls from a recorded run, in call order; a recorded failure is raised
    again. Asking for more calls than were recorded is an LLMError: the replay has diverged."""

    def __init__(self, path: Union[str, Path], run_id: str, agent: str):
        self._calls = ResponseLog(path, run_id).calls(agent)
        self._next = 0
        self.model_name = (self._calls[0]["model"] if self._calls else "replay") + " (replay)"

    def complete(self, messages: Sequence[Message], *, temperature: float, json_mode: bool = True,
                 schema: Optional[dict] = None) -> Completion:
        if self._next >= len(self._calls):
            raise LLMError(f"replay exhausted after {len(self._calls)} recorded calls")
        row = self._calls[self._next]
        self._next += 1
        if row["error"]:
            code, _, message = row["error"].partition(": ")
            raise (LLMTimeout if code == LLMTimeout.reason else LLMError)(message)
        return Completion(text=row["response_text"], model=self.model_name, latency_ms=0.0,
                          tokens_in=row["tokens_in"] or 0, tokens_out=row["tokens_out"] or 0)


def context_dict(context: CallContext) -> dict:
    return asdict(context)
