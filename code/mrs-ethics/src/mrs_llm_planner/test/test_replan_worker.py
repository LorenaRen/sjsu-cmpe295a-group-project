"""The replan worker: the LLM call runs in a thread, results come back through drain()."""
import threading
from types import SimpleNamespace as NS

from mrs_llm_planner.protocol import REPLAN, VERDICT
from mrs_llm_planner.replan_worker import ReplanWorker, is_current


def test_start_returns_at_once_and_the_result_is_drained_later():
    release, notified = threading.Event(), threading.Event()
    worker = ReplanWorker(call=lambda a: (release.wait(5), f"plan for {a}")[1], notify=notified.set)
    t = worker.start("attempt 1")
    assert worker.busy and worker.drain() == []          # the caller (a ROS callback) is not blocked
    release.set()
    t.join(5)
    assert notified.is_set() and not worker.busy
    assert worker.drain() == [("attempt 1", "plan for attempt 1")]
    assert worker.drain() == []


def test_an_exception_in_the_call_becomes_a_result():
    def call(a):
        raise RuntimeError("replanner bug")
    worker = ReplanWorker(call=call, notify=lambda: None, on_error=lambda a, e: ("failed", str(e)))
    worker.start("x").join(5)
    assert worker.drain() == [("x", ("failed", "replanner bug"))]


def test_results_keep_their_order():
    worker = ReplanWorker(call=lambda a: a * 10, notify=lambda: None)
    for i in range(3):
        worker.start(i).join(5)
    assert worker.drain() == [(0, 0), (1, 10), (2, 20)]


def test_is_current():
    action = NS(transaction_id="tx1", attempt=2)
    now = dict(state=REPLAN, replanning_state=REPLAN, transaction_id="tx1", attempt=1)
    assert is_current(action, **now)
    assert not is_current(action, **{**now, "state": VERDICT})          # deadline passed: fallback already sent
    assert not is_current(action, **{**now, "transaction_id": "tx2"})   # a new planning cycle
    assert not is_current(action, **{**now, "attempt": 2})              # an older attempt's late answer
