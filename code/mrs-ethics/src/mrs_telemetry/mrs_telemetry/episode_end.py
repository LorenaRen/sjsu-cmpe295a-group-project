"""When a recorded episode is over (Module 7). Pure Python.

Today an episode is over when its planning cycle is: the robots got a plan on /mrs/validated_plan
(the gate's APPROVE, or the coordinator's FALLBACK when no plan was approved). A REJECT that leads to a
replan sends no ValidatedPlan, so the wait goes on through the replans. A grace period after that plan
lets the last messages (cycle_done, the verdict's events) reach the recorder.

Once the executor exists (Module 6), the robots still have to drive: the grace period then has to cover
that, or the end should come from the robots reaching their goals instead.

  watch = EndWatcher(grace_s=5.0)
  watch.arm(now)                       # the mission has been sent: plans from before do not count
  watch.on_plan(now, "APPROVE")        # a ValidatedPlan arrived
  watch.due(now)                       # True once the grace period has passed
"""
from __future__ import annotations

from typing import Optional


class EndWatcher:
    def __init__(self, grace_s: float = 5.0):
        if grace_s < 0:
            raise ValueError("grace_s must be >= 0")
        self.grace_s = grace_s
        self.armed_at: Optional[float] = None
        self.plan_at: Optional[float] = None
        self.verdict: Optional[str] = None

    def arm(self, now: float) -> None:
        self.armed_at = now

    def on_plan(self, now: float, source_verdict: str) -> bool:
        """A ValidatedPlan arrived; True if it ends the episode (the first one after the mission)."""
        if self.armed_at is None or self.plan_at is not None:
            return False
        self.plan_at, self.verdict = now, source_verdict
        return True

    def due(self, now: float) -> bool:
        return self.plan_at is not None and now >= self.plan_at + self.grace_s
