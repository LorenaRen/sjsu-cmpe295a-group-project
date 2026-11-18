"""The agents' prompts (Module 4, step 3). Pure Python.

  system_prompt(ctx)          once per agent: role, how the team talks, the map, the rules, formats
  turn_message(request, ...)  every turn: round, the robot's situation, the conversation so far

The map comes from mrs_sim's WorldMap.to_prompt_dict(); the robot's situation from
mrs_common.convert.world_state_to_summary(); the rules from guidelines.guidelines_for(), so the
agents are told exactly the rules the gate runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional, Sequence

from mrs_common.plan_schema import SCHEMA_VERSION

from .parts import fold, part_of, part_summary, task_phrase
from .routes import Graph, conflict_lines, positions_from_text, route_lines


@dataclass(frozen=True)
class PlannerContext:
    """What every turn of one agent shares."""
    robot_id: str
    team: tuple[str, ...]            # every robot of the fleet, in turn order
    map_dict: dict                   # WorldMap.to_prompt_dict()
    guidelines: tuple[str, ...]      # guidelines.guidelines_for(rules)
    max_rounds: int = 3
    node_zones: dict = field(default_factory=dict)    # node -> zone ids (routes.node_zones), for routes
    start_nodes: dict = field(default_factory=dict)   # robot -> spawn node, where "Robots now" says nothing
    conflict_hints: bool = True      # tell the agents where routes collide (S-1 checked in advance)


@dataclass(frozen=True)
class TranscriptEntry:
    speaker: str                     # a robot id, or "coordinator"
    kind: str                        # prompt, proposal, agreement, discussion
    text: str
    plan_json: str = ""              # proposals only


@dataclass(frozen=True)
class TurnRequest:
    """One turn: everything the agent is told besides the system prompt."""
    round: int
    transcript: tuple[TranscriptEntry, ...]   # this mission's turns so far, the coordinator's included
    world_summary: Optional[dict] = None      # world_state_to_summary(), or None if not known yet
    goals: dict = field(default_factory=dict)   # robot -> mission.Goal, from the opening prompt's Goals line
    notes: tuple[str, ...] = field(default_factory=tuple)


# The example uses made-up robots and places on purpose. With this mission's robots and map
# nodes in it, phi3.5 copied the example as its plan: robot_a to the first node, robot_b waiting at
# the last node for robot_a, i.e. "the robots meet", whatever the mission said.
EXAMPLE_REPLY = {
    "kind": "proposal",
    "text": "I follow robot_x through the lane once its x1 is done.",
    "plan": {"assignments": [
        {"robot_id": "robot_y", "tasks": [
            {"task_id": "y1", "action": "navigate", "target": "dock_2", "priority": 2,
             "justification": "my goal; lane free after x1", "depends_on": ["x1"]}]}],
        "rationale": "one robot in the shared lane at a time"},
}


# The example is indented on purpose. Asking phi3.5 (Ollama, format json) for compact one-line JSON
# made it loop until Ollama aborted the call ("token repeat limit reached", HTTP 500); indented
# replies cost about 20% more tokens but come back.
def system_prompt(ctx: PlannerContext) -> str:
    others = [r for r in ctx.team if r != ctx.robot_id]
    rules = "\n".join(f"{i}. {g}" for i, g in enumerate(ctx.guidelines, 1))
    me, letter = ctx.robot_id, ctx.robot_id[-1]
    return f"""You are the planning agent of robot {me}, one of {len(ctx.team)} mobile robots: {", ".join(ctx.team)}.
Each robot has its own agent ({", ".join(others) or "no teammates"} are separate LLM agents). You plan ONLY
for {me}; each teammate plans for its own robot. Together your parts form ONE team plan. Robots only
drive between named places on a map: they never carry, manipulate or assist.

HOW THE TEAM TALKS
- A coordinator runs the conversation. It opens with the mission, each robot's goal and where the
  robots are, then names who speaks next, in a fixed order. You speak only when named.
- The team plan is every robot's latest part, put together. Each turn shows you the current team plan.
- In your turn you send exactly one reply, of one kind:
  * proposal   - {me}'s part: {me}'s tasks only. It replaces your earlier part and changes the team plan.
  * agreement  - you accept the current team plan exactly as it is.
  * discussion - text only: a question, a request to a teammate, or something about your robot.
- The plan is adopted when every robot has a part and EVERY agent has accepted the current team plan
  (proposing your part counts as accepting the team plan with it). When anyone changes their part,
  everyone must accept again. Proposing your part unchanged counts as accepting.
  After {ctx.max_rounds} rounds without agreement planning fails and nobody moves, so converge: once the
  team plan is acceptable to you, agree.
- An independent safety and ethics gatekeeper checks the agreed team plan. There is only one chance:
  if it rejects the plan, nobody moves. Follow every rule below before you propose or agree.

THE MAP (positions in metres; task targets must be node names from "nodes")
{json.dumps(ctx.map_dict, separators=(",", ":"))}
Zone types: room (open area), narrow (one lane; see capacity), keep_clear (pass through, never stop),
restricted (never enter).

RULES THE TEAM PLAN MUST FOLLOW
{rules}

YOUR PART (schema version {SCHEMA_VERSION})
"plan" holds "assignments" with exactly one entry, for {me}: {{"robot_id": "{me}", "tasks": [...]}}, and a
"rationale". Do not write tasks for teammates. Tasks run in list order. A task has:
  task_id (start it with your robot's letter: "{letter}1", "{letter}2", ...), action ("navigate" | "wait" | "hold_position"),
  target (a node name), priority (1-10, 1 highest), justification (a few words),
  depends_on (task ids of yours or of teammates' tasks in the current team plan).
  navigate: stay where you are until every depends_on task is done, THEN drive to the target.
  hold_position: like navigate; as the last task, the robot parks at the target.
  wait: drive to the target NOW, then stay there until every depends_on task is done.
To let a teammate go first, give your NAVIGATE task a depends_on on the teammate's task; your robot
stays put until that task is done. A wait task does not hold you back: it drives first.
Routes are always the shortest path on the map; you choose targets and order, not routes.

WHAT TO PLAN
Plan exactly what the mission asks for {me}: your last task ends at your goal (the coordinator's
"Goals" line; a part that misses it is refused). Every turn shows each robot's route (routes are the
shortest paths). If your route crosses a narrow zone that a teammate's route also crosses, one of you
must wait: the later robot's task gets a depends_on on the other robot's task that takes it through
and out of the zone. Do not go to a teammate's goal.

ONLY TASKS COUNT
The robots execute the tasks in the team plan, nothing else. What anyone writes in "text" or in a
justification changes nothing: a robot waits only if one of its tasks has a depends_on. Before you
accept, read every robot's tasks (the "tasks:" lines and the current team plan), not their words.
If you mean to wait, put it in your tasks as a depends_on; saying it is not enough.

REPLY FORMAT
Reply with exactly one JSON object and nothing else: {{"kind": ..., "text": ..., "plan": ...}}.
Keep it short: "text" is one or two short sentences, each justification a few words.
"plan" is required for a proposal. An agreement or a discussion has no "plan".
Example of a proposal by an agent of "robot_y" whose teammate "robot_x" already has a task "x1" in the
team plan. It shows the format only: robot_x, robot_y, dock_2 and x1 are made up. Never copy them;
use {me}, your goal and this map's nodes.
{json.dumps(EXAMPLE_REPLY, indent=1)}
Example of an agreement:
{{"kind": "agreement", "text": "I accept the current team plan."}}
"""


def plan_summary(plan_json: str) -> str:
    """One line per plan: 'robot_a: a1 navigate room_e | robot_b: b1 navigate room_w (starts after a1)'."""
    try:
        data = json.loads(plan_json)
        out = []
        for a in data["assignments"]:
            tasks = [task_phrase(t) for t in a["tasks"]]
            out.append(f"{a['robot_id']}: {', '.join(tasks) or 'no tasks'}")
        return " | ".join(out)
    except (ValueError, KeyError, TypeError):
        return "(plan not readable)"


def _route_section(req: TurnRequest, ctx: PlannerContext, view) -> list[str]:
    graph = Graph(ctx.map_dict, ctx.node_zones)
    starts = dict(ctx.start_nodes)
    opening = next((e.text for e in req.transcript if e.speaker == "coordinator"), "")
    starts.update(positions_from_text(opening))
    out = ["Routes of the current team plan (each task drives the shortest path):"]
    out += route_lines(graph, view.parts, starts, ctx.team)
    if ctx.conflict_hints:
        conflicts = conflict_lines(graph, view.parts, starts, ctx.team)
        out += conflicts or ["  No two robots cross the same capacity-1 zone without an order."]
    return out


def turn_message(req: TurnRequest, ctx: PlannerContext) -> str:
    """The turn's user message: the conversation (each part as one line), then the current team plan
    in full and who has accepted it. Folded with parts.fold(), exactly as the coordinator does."""
    lines = [f"Round {req.round} of {ctx.max_rounds}. It is your turn, {ctx.robot_id}."]
    if req.world_summary is not None:
        lines.append(f"Your robot now: {json.dumps(req.world_summary, separators=(',', ':'))}")
    else:
        lines.append("Your robot's sensor state is not known yet; where each robot is stands in the "
                     "coordinator's \"Robots now\" line.")
    lines.append("")
    lines.append("Conversation so far:")

    seen: list = []
    for e in req.transcript:
        before = fold(seen, ctx.team)
        seen.append(e)
        after = fold(seen, ctx.team)
        if e.kind == "proposal" and e.speaker in ctx.team:
            part = part_of(e.plan_json, e.speaker)
            if after.change != before.change:
                lines.append(f"- {e.speaker} PROPOSES ITS PART (team plan version {after.change}): {e.text}\n"
                             f"  tasks (what {e.speaker} will actually do): {part_summary(part)}")
            else:
                lines.append(f"- {e.speaker} PROPOSES ITS PART UNCHANGED, which accepts team plan version "
                             f"{after.change}. {e.text}".rstrip())
        elif e.kind == "agreement" and after.change:
            lines.append(f"- {e.speaker} ACCEPTS team plan version {after.change}. {e.text}".rstrip())
        else:
            lines.append(f"- {e.speaker}: {e.text}")

    view = fold(req.transcript, ctx.team)
    lines.append("")
    if not view.change:
        lines.append("Team plan: none yet. No robot has proposed its part.")
    else:
        data = view.plan_dict(ctx.team)
        plan, problem = view.team_plan(ctx.team)
        lines.append(f"Current team plan (version {view.change}): {json.dumps(data, separators=(',', ':'))}")
        missing = view.missing(ctx.team)
        if missing:
            lines.append(f"Parts still missing: {', '.join(missing)}.")
        if problem:
            lines.append(f"Problem in the team plan: {problem}. It cannot be adopted until a part is changed.")
        lines += _route_section(req, ctx, view)
        needed = [r for r in ctx.team if r not in view.accepted]
        lines.append("What each robot will actually do (only tasks count, not words): " + "; ".join(
            f"{r}: {part_summary(view.parts[r])}" for r in ctx.team if r in view.parts) + ".")
        lines.append(f"Accepted so far by: {', '.join(view.accepted)}; still needed: {', '.join(needed) or 'nobody'}.")
    if ctx.robot_id in view.parts:
        lines.append(f"Your current part: {part_summary(view.parts[ctx.robot_id])}.")
    else:
        lines.append("You have not proposed your part yet.")
    for note in req.notes:
        lines.append(f"Note: {note}")
    lines.append("Reply with one JSON object.")
    return "\n".join(lines)


def transcript_digest(transcript: Sequence[TranscriptEntry], max_len: int = 200) -> str:
    """A short summary for ProposedPlan.dialogue_digest, e.g. 'robot_a proposal; robot_b agreement'."""
    text = "; ".join(f"{e.speaker} {e.kind}" for e in transcript if e.speaker != "coordinator")
    return text if len(text) <= max_len else text[: max_len - 3] + "..."
