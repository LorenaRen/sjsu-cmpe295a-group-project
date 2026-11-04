"""How a robot is at a node in its compiled plan, for S-4 (pure Python).

  presence(compiled, team_plan, robot, step, node) -> Presence

A robot is at a node standing there (a standing step, or the end of its plan) or driving
through it (a waypoint it does not stop at, including the start node it drives off from).
S-4's constraints (verdict.py), and which robot has to change its plan (team_check.py),
depend on:
  - the step, and the task, that bring it there; none if it is there from the start of its plan;
  - the step, and the task, that take it away; none if it stays to the end of its plan;
  - whether it drives through, and whether it stands there at the end of a task that goes there.
"""
from __future__ import annotations

from typing import NamedTuple

from mrs_common.plan_schema import TeamPlan

from ..plan import TIME_EPS, Action
from .compiler import CompiledTeamPlan


class Presence(NamedTuple):
    robot: str
    node: str
    passes: bool              # drives through the node; otherwise stands there
    arrive_step: int | None   # the drive that brings it there; None: there from the start of its plan
    leave_step: int | None    # the step that takes it away; None: there to the end of its plan
    arrive_task: str | None   # the tasks of those steps
    leave_task: str | None
    task: str | None          # the task it is on while there
    ends_task: bool           # stands there at the end of `task`, whose target is the node

    @property
    def from_start(self) -> bool:
        return self.arrive_step is None

    @property
    def stays(self) -> bool:
        return self.leave_step is None


def presence(compiled: CompiledTeamPlan, team_plan: TeamPlan, robot: str, step: int, node: str) -> Presence:
    """How `robot` is at `node` in `step` of its compiled plan (the step S-4 refers to)."""
    steps = compiled.plans[robot].steps
    s = steps[step]
    if s.action == Action.NAVIGATE:
        if step == 0 and s.waypoints and s.waypoints[0].node == node \
                and abs(s.waypoints[0].t - s.t_start) <= TIME_EPS:
            arrive, leave = None, step                     # drives off from its start node
        elif node == s.end_node:
            if step + 1 < len(steps) and steps[step + 1].action != Action.NAVIGATE:
                return presence(compiled, team_plan, robot, step + 1, node)   # it stops there
            arrive, leave = step, (step + 1 if step + 1 < len(steps) else None)
        else:
            arrive, leave = step, step                     # drives through on the way
        passes = leave is not None
    else:
        arrive = step
        while arrive > 0 and steps[arrive - 1].action != Action.NAVIGATE:
            arrive -= 1
        arrive = arrive - 1 if arrive > 0 else None
        leave = step
        while leave + 1 < len(steps) and steps[leave + 1].action != Action.NAVIGATE:
            leave += 1
        leave = leave + 1 if leave + 1 < len(steps) else None
        passes = False
    origins = compiled.origins[robot]
    task = origins[step].task_id
    return Presence(
        robot=robot, node=node, passes=passes, arrive_step=arrive, leave_step=leave,
        arrive_task=origins[arrive].task_id if arrive is not None else None,
        leave_task=origins[leave].task_id if leave is not None else None,
        task=task,
        ends_task=not passes and task is not None and team_plan.task(task).target == node,
    )


def only_the_keeper_can_resolve(yielder: Presence, keeper: Presence) -> bool:
    """Whether waiting cannot resolve the conflict, so that the keeper has to change its plan
    instead of the yielder R-1 chose: the keeper stays at the node to the end of its plan, and
    the yielder has to be there after it gets there, to drive through or to end a task there.
    A yielder there from the start of its plan can still leave first; one that only waits
    there between tasks can wait somewhere else."""
    return keeper.stays and not yielder.from_start and (yielder.passes or yielder.ends_task)
