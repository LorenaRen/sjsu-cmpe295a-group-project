from mrs_llm_planner.llm_client import FakeClient, LLMTimeout, Message
from mrs_llm_planner.response_log import CallContext, LoggingClient, ResponseLog
from mrs_llm_planner.summary import main, summarize


def test_summary(tmp_path, capsys):
    path = tmp_path / "log.sqlite"
    log = ResponseLog(path, "r1")
    c = LoggingClient(FakeClient(script=["a", "b", LLMTimeout("slow")]), log, "robot_a")
    c.context = CallContext("ep", "tx", 0, 1)
    c.complete([Message("user", "x")], temperature=0.7)
    c.complete([Message("user", "x")], temperature=0.2)          # a repair: same turn
    c.context = CallContext("ep", "tx", 0, 2)
    try:
        c.complete([Message("user", "x")], temperature=0.7)
    except LLMTimeout:
        pass
    log.record_cycle(episode_id="ep", transaction_id="tx", mission="m", model="fake",
                     stats={"outcome": "fallback", "cause": "rejected", "rounds": 2, "turns": 3, "skips": 1,
                            "plan_versions": 2}, duration_s=12.5)
    log.record_cycle(episode_id="ep", transaction_id="tx2", mission="m", model="fake",
                     stats={"outcome": "approved", "cause": "", "rounds": 2, "turns": 3, "skips": 0,
                            "plan_versions": 2}, duration_s=7.5)
    [s] = summarize(path)
    assert (s["cycles"], s["approved"], s["fallback"], s["fallback_causes"]) == (2, 1, 1, {"rejected": 1})
    assert (s["llm_calls"], s["repair_calls"], s["failed_calls"], s["skipped_turns"]) == (3, 1, 1, 1)
    assert s["mean_duration_s"] == 10.0 and s["failed_by_reason"] == {"llm_timeout": 1}
    assert main(["--log", str(path)]) == 0
    assert "approved 1 (50%), fallback 1 [rejected 1]" in capsys.readouterr().out
    assert main(["--log", str(tmp_path / "missing.sqlite")]) == 1
