"""Team plans built from parts: each agent proposes only its own robot's tasks (Module 4). Pure Python.

  view = fold(transcript, team)        # what the dialogue so far adds up to
  view.parts                           # robot -> its latest part (an Assignment as a dict)
  view.change                          # how many times the team plan changed (0 = no part yet)
  view.accepted                        # robots that accepted the current team plan
  view.team_plan(team)                 # (TeamPlan or None, problem text)
  view.agreed(team)                    # every robot has a part, the plan is valid, everyone accepted

Rules, the same for the coordinator (protocol.py) and every agent's prompt (prompts.py), which
both call fold() on the same transcript:

  proposal   the speaker's assignment in the turn's plan becomes its part (other robots'
             assignments in that plan are ignored: they speak for themselves). A changed part
             changes the team plan: change + 1, and only the speaker has accepted it. A part
             equal to the speaker's current one changes nothing and counts as accepting.
  agreement  accepts the current team plan (ignored before any part exists).
  discussion changes nothing.

The plan_json an agent publishes is the whole team plan with its new part merged in (agent.py),
so every DialogueTurn carries a complete, valid TeamPlan even when the part depends on a
teammate's task.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from mrs_common.plan_schema import SCHEMA_VERSION, TeamPlan
from pydantic import ValidationError


SKIP_PREFIX = "I skip this turn"   # how an agent's skip message begins (agent.failure_text)


def part_of(plan, robot: str) -> Optional[dict]:
    """The robot's assignment in a plan (TeamPlan, dict or JSON); repeated entries merged."""
    if isinstance(plan, TeamPlan):
        data = json.loads(plan.model_dump_json())
    elif isinstance(plan, str):
        try:
            data = json.loads(plan)
        except ValueError:
            return None
    else:
        data = plan
    if not isinstance(data, dict) or not isinstance(data.get("assignments"), list):
        return None
    tasks, found = [], False
    for a in data["assignments"]:
        if isinstance(a, dict) and a.get("robot_id") == robot and isinstance(a.get("tasks"), list):
            found = True
            tasks += a["tasks"]
    return {"robot_id": robot, "tasks": tasks} if found else None


def part_key(part: Optional[dict]) -> tuple:
    """What makes two parts the same: task ids, actions, targets and dependencies, in order."""
    if not part:
        return ()
    return tuple((t.get("task_id"), t.get("action"), t.get("target"), tuple(sorted(t.get("depends_on") or [])))
                 for t in part.get("tasks", []) if isinstance(t, dict))


def merge(parts: dict, team: Sequence[str], rationale: str = "") -> dict:
    """The team plan as a dict: the parts in team order."""
    return {"schema_version": SCHEMA_VERSION, "rationale": rationale[:2000],
            "assignments": [parts[r] for r in team if r in parts]}


@dataclass
class TeamView:
    parts: dict = field(default_factory=dict)       # robot -> assignment dict
    notes: dict = field(default_factory=dict)       # robot -> the text of its latest part
    change: int = 0
    accepted: list = field(default_factory=list)

    def missing(self, team: Sequence[str]) -> list[str]:
        return [r for r in team if r not in self.parts]

    def plan_dict(self, team: Sequence[str]) -> Optional[dict]:
        if not self.parts:
            return None
        rationale = " ".join(f"{r}: {self.notes[r]}" for r in team if self.notes.get(r))
        return merge(self.parts, team, rationale)

    def team_plan(self, team: Sequence[str]) -> tuple[Optional[TeamPlan], str]:
        data = self.plan_dict(team)
        if data is None:
            return None, "no robot has proposed its part yet"
        try:
            return TeamPlan.model_validate(data), ""
        except ValidationError as e:
            return None, "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())

    def agreed(self, team: Sequence[str]) -> bool:
        plan, problem = self.team_plan(team)
        return plan is not None and not self.missing(team) and set(self.accepted) >= set(team)


def fold(entries: Iterable, team: Sequence[str]) -> TeamView:
    """entries: TranscriptEntry-like (speaker, kind, text, plan_json), in order."""
    view = TeamView()
    for e in entries:
        if e.speaker not in team:
            continue
        if e.kind == "proposal":
            part = part_of(e.plan_json, e.speaker)
            if part is None:
                continue
            if part_key(part) == part_key(view.parts.get(e.speaker)):
                if e.speaker not in view.accepted:
                    view.accepted.append(e.speaker)
                continue
            view.parts[e.speaker] = part
            view.notes[e.speaker] = e.text
            view.change += 1
            view.accepted = [e.speaker]
        elif e.kind == "agreement" and view.change:
            if e.speaker not in view.accepted:
                view.accepted.append(e.speaker)
    return view


def task_phrase(t: dict) -> str:
    """What the task makes the robot do, timing included:
    'a1 navigate room_e', 'b1 navigate room_w (starts after a1)',
    'a1 wait room_e_n (drives there now, then waits for b1)'."""
    head = f"{t.get('task_id')} {t.get('action')} {t.get('target')}"
    deps = ",".join(t.get("depends_on") or [])
    if not deps:
        return head
    if t.get("action") == "wait":
        return f"{head} (drives there now, then waits for {deps})"
    return f"{head} (starts after {deps})"


def part_summary(part: Optional[dict]) -> str:
    """'a1 navigate room_e, a2 hold_position room_e (starts after b1)'."""
    if not part:
        return "(no part yet)"
    return ", ".join(task_phrase(t) for t in part.get("tasks", []) if isinstance(t, dict)) or "no tasks"
