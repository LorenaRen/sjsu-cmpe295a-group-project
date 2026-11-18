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
