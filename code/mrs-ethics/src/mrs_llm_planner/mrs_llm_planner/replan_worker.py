"""Run replan LLM calls off the ROS thread (Module 4). Pure Python.

A replan is an LLM call that can take a minute. Made inside a ROS callback, it would freeze the
coordinator node for that long: no messages, and no timer, so the protocol's replan deadline
(replan_timeout_s) and cycle deadline could not fire. Instead, the node hands the call to this
worker, which runs it in a background thread and queues the result. `notify` (in the node: a guard
condition's trigger) wakes the node, which takes the results with drain() on its own thread. The
protocol (protocol.Coordinator) is therefore only ever touched by one thread, and needs no locks.

  worker = ReplanWorker(call=lambda action: ..., notify=guard_condition.trigger)
  worker.start(action)                     # returns at once
  for action, result in worker.drain():    # later, on the node's thread
      ...

A result may arrive after the protocol has given up on it (deadline passed, cycle over): the node
compares it with the protocol's state before using it (is_current), and drops it otherwise.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional


class ReplanWorker:
    def __init__(self, call: Callable[[Any], Any], notify: Callable[[], None],
                 on_error: Optional[Callable[[Any, BaseException], Any]] = None):
        """call(action) -> result runs in the worker thread. on_error(action, exc) -> result turns an
        exception in call into a result (default: re-raise into the thread, losing the result)."""
        self._call = call
        self._notify = notify
        self._on_error = on_error
        self._results: "queue.Queue[tuple[Any, Any]]" = queue.Queue()
        self._threads: list[threading.Thread] = []

    def start(self, action) -> threading.Thread:
        t = threading.Thread(target=self._run, args=(action,), daemon=True, name="replan")
        self._threads = [x for x in self._threads if x.is_alive()] + [t]
        t.start()
        return t

    def _run(self, action) -> None:
        try:
            result = self._call(action)
        except BaseException as e:   # noqa: BLE001 - a failed call must still report back
            if self._on_error is None:
                raise
            result = self._on_error(action, e)
        self._results.put((action, result))
        self._notify()

    def drain(self) -> list[tuple[Any, Any]]:
        """Every result that has arrived, oldest first."""
        out = []
        while True:
            try:
                out.append(self._results.get_nowait())
            except queue.Empty:
                return out

    @property
    def busy(self) -> bool:
        return any(t.is_alive() for t in self._threads)


def is_current(action, *, state: str, replanning_state: str, transaction_id: str, attempt: int) -> bool:
    """Whether a replan result still answers what the protocol is waiting for: it is replanning,
    for the same planning cycle, and this result is for the attempt it started (protocol.attempt + 1)."""
    return (state == replanning_state and action.transaction_id == transaction_id
            and action.attempt == attempt + 1)
