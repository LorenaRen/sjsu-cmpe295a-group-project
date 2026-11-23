import pytest

from mrs_llm_planner.llm_client import FakeClient, LLMError, LLMTimeout, Message
from mrs_llm_planner.response_log import CallContext, LoggingClient, ReplayClient, ResponseLog

MSGS = [Message("user", "turn")]


def test_logs_successes_and_failures_and_replays(tmp_path):
    path = tmp_path / "log.sqlite"
    log = ResponseLog(path, run_id="r1")
    client = LoggingClient(FakeClient(script=["a", LLMTimeout("slow"), "b"]), log, agent="robot_a")
    client.context = CallContext(episode_id="e", transaction_id="t", attempt=1, round=2)
    assert client.complete(MSGS, temperature=0.7).text == "a"
    with pytest.raises(LLMTimeout):
        client.complete(MSGS, temperature=0.7)
    client.complete(MSGS, temperature=0.7)

    rows = log.calls("robot_a")
    assert [r["call_no"] for r in rows] == [1, 2, 3]
    assert rows[0]["attempt"] == 1 and rows[0]["round"] == 2 and rows[0]["transaction_id"] == "t"
    assert rows[1]["response_text"] is None and rows[1]["error"].startswith("llm_timeout")
    assert log.totals()["calls"] == 3 and log.totals()["failed"] == 1

    replay = ReplayClient(path, run_id="r1", agent="robot_a")
    assert replay.complete(MSGS, temperature=0).text == "a"
    with pytest.raises(LLMTimeout):
        replay.complete(MSGS, temperature=0)
    assert replay.complete(MSGS, temperature=0).text == "b"
    with pytest.raises(LLMError):
        replay.complete(MSGS, temperature=0)


def test_runs_and_agents_are_separate(tmp_path):
    path = tmp_path / "log.sqlite"
    LoggingClient(FakeClient(script=["x"]), ResponseLog(path, "r1"), "robot_a").complete(MSGS, temperature=0)
    LoggingClient(FakeClient(script=["y"]), ResponseLog(path, "r2"), "robot_a").complete(MSGS, temperature=0)
    LoggingClient(FakeClient(script=["z"]), ResponseLog(path, "r2"), "robot_b").complete(MSGS, temperature=0)
    assert [r["response_text"] for r in ResponseLog(path, "r2").calls()] == ["y", "z"]
    assert ResponseLog(path, "r2").calls("robot_b")[0]["call_no"] == 1


def test_logging_chat_records_replan_calls(tmp_path):
    from types import SimpleNamespace as NS

    from mrs_llm_planner.response_log import REPLANNER, LoggingChat

    class Chat:   # the replanner's OllamaClient interface
        model, timeout = "phi3.5", 60.0

        def __init__(self, answers):
            self.answers = list(answers)

        def chat(self, messages, json_schema=None):
            return self.answers.pop(0)

    ok = NS(success=True, content='{"assignments": []}', error_reason=None, error_detail=None,
            tokens_in=900, tokens_out=80, latency_ms=2500.0)
    bad = NS(success=False, content=None, error_reason="llm_timeout", error_detail="no answer in 60 s",
             tokens_in=0, tokens_out=0, latency_ms=60000.0)
    log = ResponseLog(tmp_path / "log.sqlite", run_id="r1")
    chat = LoggingChat(Chat([ok, bad]), log, agent=REPLANNER, model="ollama/phi3.5", temperature=0.7,
                       context=CallContext("ep", "tx", 1, 0))
    msgs = [{"role": "system", "content": "fix it"}, {"role": "user", "content": "plan + constraints"}]
    assert chat.chat(msgs, json_schema={"type": "object"}) is ok
    chat.context = CallContext("ep", "tx", 2, 0)
    assert chat.chat(msgs) is bad
    assert chat.model == "phi3.5" and chat.timeout == 60.0     # the wrapped client's attributes

    rows = log.calls(REPLANNER)
    assert [r["attempt"] for r in rows] == [1, 2] and all(r["transaction_id"] == "tx" for r in rows)
    assert rows[0]["response_text"] == '{"assignments": []}' and rows[0]["tokens_in"] == 900
    assert rows[0]["temperature"] == 0.7 and rows[0]["model"] == "ollama/phi3.5"
    assert '"plan + constraints"' in rows[0]["request_json"]
    assert rows[1]["response_text"] is None and rows[1]["error"] == "llm_timeout: no answer in 60 s"
