"""The coordinator's dialogue protocol as a state machine (Module 4). Pure Python, no ROS.

The coordinator node feeds it events and carries out the actions it returns:

  start_cycle(mission, episode_id, now)       a new mission (text, or a Mission with goals): mints the transaction_id
  on_turn(turn, now)                          an agent's DialogueTurn (prompts are ignored)
  on_turn_failed(speaker, reason, ...)        the named agent gave up (optional; tick() catches it too)
  on_verdict(verdict, now)                    the gate's EthicalVerdict
  tick(now)                                   call periodically; reports a missed deadline

  actions: PauseWorld, ResumeWorld, SendPrompt, SubmitPlan, Fallback, CycleDone

The protocol (implementation guide, Module 4), basic version: one dialogue per mission, one plan
to the gate, no replanning.
  - Round-robin: in every round each agent speaks once, in team order. Before every agent turn the
    coordinator sends a prompt naming the next speaker. The first prompt carries the mission and
    where the robots are.
  - Parts (parts.py): each agent proposes only its own robot's part; the team plan is every
    robot's latest part, merged. A changed part changes the team plan, and only its proposer has
    accepted the new version; re-proposing an unchanged part counts as accepting; an agreement
    accepts the current team plan. The plan is agreed when every robot has a part, the merged plan
    is valid, and every agent has accepted it. It is sent to the gate at once, even mid-round.
  - Mission goals (mission.py): the opening prompt lists them. Each agent checks its own
    proposals against them (with its one repair call). An agent that still cannot produce a valid
    reply (bad format, unknown places, or a missed goal) says so in a discussion turn: its turn is
    skipped and the dialogue goes on. As a safety net, an agreed plan that misses a goal never
    reaches the gate (fallback, cause mission_mismatch).
  - Agents are never silent: any failed turn is posted as a discussion ("I skip this turn: ..."),
    and the next robot speaks. Only a crashed agent node is silent; turn_timeout_s catches it and
    ends planning. cycle_timeout_s caps the whole cycle, verdict included.
  - APPROVE: done (the gate publishes the ValidatedPlan).
  - Anything else ends in the fallback: no agreement after max_rounds (nothing goes to the gate),
    a failed turn (the agent gave up, or nothing came within turn_timeout_s), a REJECT, or no
    verdict within verdict_timeout_s. The node then publishes hold_all_plan as a FALLBACK
    ValidatedPlan and a planning_exhausted EpisodeEvent.
  - DialogueTurn, ProposedPlan and EthicalVerdict keep their `attempt` field; it is always 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Sequence, Union

from mrs_common import reasons as R
from mrs_common.ids import new_transaction_id
from mrs_common.names import TRAVEL_SEP
from mrs_common.plan_schema import TeamPlan, hold_all_plan, plan_to_json

from .mission import MISSION_MISMATCH, Mission, check_goals
from .parts import SKIP_PREFIX, TeamView, fold
from .prompts import TranscriptEntry, transcript_digest

COORDINATOR = "coordinator"     # DialogueTurn.COORDINATOR, without importing mrs_msgs
ATTEMPT = 0                     # the messages' attempt field: one dialogue per mission

APPROVE, MODIFY, REJECT = "APPROVE", "MODIFY", "REJECT"


# --------------------------------------------------------------------------
# What comes in
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Turn:
    """The fields of a DialogueTurn the protocol needs."""
    transaction_id: str
    round: int
    kind: str
    speaker_id: str
    text: str = ""
    plan: Optional[TeamPlan] = None


@dataclass(frozen=True)
class Verdict:
    """The fields of an EthicalVerdict the protocol needs."""
    transaction_id: str
    verdict: str
    rule_ids: tuple[str, ...] = ()
    rationale: str = ""
    constraints_json: str = "[]"   # JSON list of Constraint objects for REJECT/MODIFY
    attempt: int = 0               # the attempt this verdict is for


# --------------------------------------------------------------------------
# What goes out
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PauseWorld:
    pass


@dataclass(frozen=True)
class ResumeWorld:
    pass


@dataclass(frozen=True)
class SendPrompt:
    transaction_id: str
    round: int
    next_speaker: str
    text: str


@dataclass(frozen=True)
class SubmitPlan:
    """Publish a ProposedPlan to the gate."""
    plan: TeamPlan
    transaction_id: str
    dialogue_digest: str
    latency_ms: float           # from the first prompt to the agreement
    attempt: int = 0            # attempt number (0 = first, +1 after each REJECT)


@dataclass(frozen=True)
class Fallback:
    """Publish hold_all_plan as a FALLBACK ValidatedPlan and a planning_exhausted EpisodeEvent."""
    transaction_id: str
    cause: str                  # R.NO_AGREEMENT, R.LLM_TIMEOUT, R.LLM_MALFORMED, ... or "rejected", "no_verdict"
    detail: str


@dataclass(frozen=True)
class CycleDone:
    transaction_id: str
    outcome: str                # "approved" or "fallback"


@dataclass(frozen=True)
class StartReplan:
    """Trigger a replan cycle with the LLM after a REJECT."""
    transaction_id: str
    attempt: int                # the new attempt number
    plan: TeamPlan              # the rejected plan
    constraints_json: str       # constraints from the REJECT verdict
    rationale: str              # rejection rationale


Action = Union[PauseWorld, ResumeWorld, SendPrompt, SubmitPlan, Fallback, CycleDone, StartReplan]


@dataclass(frozen=True)
class ProtocolConfig:
    team: tuple[str, ...]
    max_rounds: int = 3
    turn_timeout_s: float = 150.0
    verdict_timeout_s: float = 30.0
    pause_world: bool = False
    cycle_timeout_s: float = 300.0     # hard limit for a whole planning cycle
    max_replan_attempts: int = 3       # max attempts after REJECT (0 = no replan, fallback immediately)
    replan_timeout_s: float = 60.0     # timeout for each replan LLM call

    def __post_init__(self):
        if not self.team:
            raise ValueError("the team is empty")
        if len(set(self.team)) != len(self.team):
            raise ValueError(f"the team names a robot twice: {list(self.team)}")
        if COORDINATOR in self.team:
            raise ValueError(f"'{COORDINATOR}' cannot be a robot id")
        if self.max_rounds < 1:
            raise ValueError("max_rounds must be at least 1")
        if self.turn_timeout_s <= 0 or self.verdict_timeout_s <= 0 or self.cycle_timeout_s <= 0:
            raise ValueError("timeouts must be positive")
        if self.max_replan_attempts < 0:
            raise ValueError("max_replan_attempts must be non-negative")


# --------------------------------------------------------------------------
# The state machine
# --------------------------------------------------------------------------
IDLE, TURN, VERDICT, REPLAN, DONE = "idle", "waiting_for_turn", "waiting_for_verdict", "replanning", "done"


class Coordinator:
    def __init__(self, config: ProtocolConfig, *, new_id: Callable[[], str] = new_transaction_id,
                 start_nodes: Optional[Mapping[str, str]] = None):
        """start_nodes: robot -> its spawn node on the map. Where /mrs/team_state has not reported a
        robot, the opening prompt gives this instead: the position the gate assumes (start_source
        spawns), so the agents plan from the same positions the gate checks against."""
        self.start_nodes = dict(start_nodes or {})
        self.config = config
        self._new_id = new_id
        self.state = IDLE
        self.mission = ""
        self.goals: dict = {}   # robot -> mission.Goal
        self.goals_line = ""
        self.episode_id = ""
        self.transaction_id = ""
        self.positions: dict[str, str] = {}   # robot -> node or edge ("a->b"); the node keeps it current
        self.deadline: Optional[float] = None
        self.outcome = ""
        self._reset(0.0)

    def _reset(self, now: float) -> None:
        self.started = now
        self.round = 1
        self.speaker_index = 0
        self._transcript: list[TranscriptEntry] = []
        self.view = TeamView()
        self.cause = self.detail = ""
        self.attempt = 0                                      # current attempt (0 = first, +1 after each REJECT)
        self.last_submitted_plan: Optional[TeamPlan] = None   # the plan we last sent to the gate

    # ---- queries ---------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self.state in (TURN, VERDICT, REPLAN)

    @property
    def next_speaker(self) -> str:
        return self.config.team[self.speaker_index] if self.state == TURN else ""

    @property
    def proposal(self) -> Optional[TeamPlan]:
        """The current team plan (every part so far, merged), or None if there is none or it is invalid."""
        return self.view.team_plan(self.config.team)[0]

    @property
    def accepted(self) -> list[str]:
        return list(self.view.accepted)

    @property
    def transcript(self) -> tuple[TranscriptEntry, ...]:
        return tuple(self._transcript)

    # ---- events ----------------------------------------------------------------------------
    def start_cycle(self, mission: Union[str, Mission], episode_id: str, now: float) -> list[Action]:
        """A planning cycle for a new mission, abandoning any cycle still running. A plain string
        is a mission without goals; parse it with mission.parse_mission() to get them."""
        if isinstance(mission, str):
            mission = Mission(text=mission.strip())
        if not mission.text.strip():
            raise ValueError("empty mission")
        actions: list[Action] = []
        if self.config.pause_world and not self.busy:
            actions.append(PauseWorld())
        self.mission = mission.text.strip()
        self.goals = dict(mission.goals)
        self.goals_line = mission.goals_line()
        self.episode_id = episode_id
        self.transaction_id = self._new_id()
        self.outcome = ""
        self._reset(now)
        opening = self._opening_text()
        self._transcript.append(TranscriptEntry(COORDINATOR, "prompt", opening))
        self.state = TURN
        return actions + [self._prompt(now, opening)]

    def on_turn(self, turn: Turn, now: float) -> list[Action]:
        """An agent's turn. Ignored unless it is the turn the coordinator is waiting for."""
        if (self.state != TURN or turn.speaker_id == COORDINATOR or turn.kind == "prompt"
                or turn.transaction_id != self.transaction_id or turn.round != self.round
                or turn.speaker_id != self.next_speaker):
            return []
        if turn.kind == "proposal":
            if turn.plan is None:
                return self._fallback(R.LLM_MALFORMED, f"{turn.speaker_id} proposed without a plan")
            self._transcript.append(TranscriptEntry(turn.speaker_id, "proposal", turn.text, plan_to_json(turn.plan)))
        elif turn.kind == "agreement":
            self._transcript.append(TranscriptEntry(turn.speaker_id, "agreement", turn.text))
        else:
            self._transcript.append(TranscriptEntry(turn.speaker_id, "discussion", turn.text))
        self.view = fold(self._transcript, self.config.team)

        if self.view.agreed(self.config.team):
            plan = self.proposal
            missed = check_goals(plan, self.goals)
            if missed:   # the agents check their own goals; a plan that slipped through never reaches the gate
                return self._fallback(MISSION_MISMATCH, "; ".join(missed))
            self.last_submitted_plan = plan
            self.state, self.deadline = VERDICT, now + self.config.verdict_timeout_s
            return [SubmitPlan(plan, self.transaction_id, transcript_digest(self._transcript),
                               latency_ms=(now - self.started) * 1000.0, attempt=self.attempt)]
        return self._advance(now)

    def on_turn_failed(self, speaker: str, reason: str, detail: str, now: float) -> list[Action]:
        if self.state != TURN or speaker != self.next_speaker:
            return []
        return self._fallback(reason, f"{speaker}: {detail}")

    def on_verdict(self, verdict: Verdict, now: float) -> list[Action]:
        if self.state != VERDICT or verdict.transaction_id != self.transaction_id:
            return []
        if verdict.verdict in (APPROVE, MODIFY):     # the gate publishes the ValidatedPlan itself
            return self._finish("approved")
        # REJECT: try to replan if attempts remain
        rules = ", ".join(verdict.rule_ids) or "no rule id"
        if self.config.max_replan_attempts > 0 and self.attempt < self.config.max_replan_attempts:
            # Trigger replan with the LLM
            self.state = REPLAN
            self.deadline = now + self.config.replan_timeout_s
            return [StartReplan(
                transaction_id=self.transaction_id,
                attempt=self.attempt + 1,
                plan=self.last_submitted_plan,
                constraints_json=verdict.constraints_json,
                rationale=verdict.rationale,
            )]
        return self._fallback("rejected", f"{rules}: {verdict.rationale}".rstrip(": "))

    def on_replan_result(self, success: bool, plan: Optional[TeamPlan], error_reason: str,
                         error_detail: str, now: float) -> list[Action]:
        """Handle the result of a replan attempt from the LLM."""
        if self.state != REPLAN:
            return []
        if not success:
            # Replan failed (LLM error, timeout, malformed output)
            return self._fallback(error_reason, error_detail)
        # Replan succeeded: submit the new plan
        self.attempt += 1
        self.last_submitted_plan = plan
        self.state, self.deadline = VERDICT, now + self.config.verdict_timeout_s
        return [SubmitPlan(plan, self.transaction_id, f"replan attempt {self.attempt}",
                           latency_ms=(now - self.started) * 1000.0, attempt=self.attempt)]

    def tick(self, now: float) -> list[Action]:
        if self.busy and now >= self.started + self.config.cycle_timeout_s:
            return self._fallback("cycle_timeout", f"no approved plan within {self.config.cycle_timeout_s:g} s")
        if self.deadline is None or now < self.deadline:
            return []
        if self.state == TURN:
            return self._fallback(R.LLM_TIMEOUT,
                                  f"{self.next_speaker} sent no turn within {self.config.turn_timeout_s:g} s")
        if self.state == VERDICT:
            return self._fallback("no_verdict", f"no verdict within {self.config.verdict_timeout_s:g} s")
        if self.state == REPLAN:
            return self._fallback(R.LLM_TIMEOUT, f"replan attempt {self.attempt + 1} timed out")
        return []

    # ---- internals ---------------------------------------------------------------------------
    def _opening_text(self) -> str:
        lines = [f"Mission: {self.mission}"]
        if self.goals_line:
            lines.append(self.goals_line)
        lines += self._positions_lines()
        lines.append(f"Agree on one plan for {', '.join(self.config.team)} within {self.config.max_rounds} rounds. "
                     f"{self.config.team[0]} speaks first.")
        return "\n".join(lines)

    def _positions_lines(self) -> list[str]:
        """'Robots now: ...' from TeamState; a robot it has not reported gets its start node."""
        reported, started = [], []
        for r in self.config.team:
            if self.positions.get(r):
                reported.append(f"{r} at {self.positions[r]}")
            elif self.start_nodes.get(r):
                started.append(f"{r} at {self.start_nodes[r]}")
        if reported and started:
            return ["Robots now: " + "; ".join(reported) + "; " + "; ".join(started) + " (start position)."]
        if reported:
            return ["Robots now: " + "; ".join(reported) + "."]
        if started:
            return ["Robots now (start positions): " + "; ".join(started) + "."]
        return []

    def _prompt(self, now: float, text: str) -> SendPrompt:
        self.deadline = now + self.config.turn_timeout_s
        return SendPrompt(self.transaction_id, self.round, self.next_speaker, text)

    def _advance(self, now: float) -> list[Action]:
        self.speaker_index += 1
        if self.speaker_index == len(self.config.team):
            if self.round == self.config.max_rounds:
                return self._fallback(R.NO_AGREEMENT,
                                      f"no plan accepted by every agent after {self.config.max_rounds} rounds")
            self.speaker_index, self.round = 0, self.round + 1
        return [self._prompt(now, f"Round {self.round}: {self.next_speaker}, your turn.")]

    def _fallback(self, cause: str, detail: str) -> list[Action]:
        self.cause, self.detail = cause, detail
        return [Fallback(self.transaction_id, cause, detail)] + self._finish("fallback")

    def stats(self) -> dict:
        """What this cycle's dialogue looked like, for the run's cycle record (summary.py)."""
        turns = [e for e in self._transcript if e.speaker in self.config.team]
        kinds = [e.kind for e in turns]
        return {"outcome": self.outcome, "cause": self.cause, "detail": self.detail[:500],
                "rounds": self.round, "turns": len(turns), "proposals": kinds.count("proposal"),
                "agreements": kinds.count("agreement"),
                "skips": sum(e.kind == "discussion" and e.text.startswith(SKIP_PREFIX) for e in turns),
                "discussions": sum(e.kind == "discussion" and not e.text.startswith(SKIP_PREFIX) for e in turns),
                "plan_versions": self.view.change}

    def _finish(self, outcome: str) -> list[Action]:
        self.state, self.deadline, self.outcome = DONE, None, outcome
        actions: list[Action] = [CycleDone(self.transaction_id, outcome)]
        if self.config.pause_world:
            actions.append(ResumeWorld())
        return actions


# --------------------------------------------------------------------------
# The fallback plan
# --------------------------------------------------------------------------
def hold_targets(team: Sequence[str], positions: Mapping[str, str], spawn_nodes: Mapping[str, str]) -> dict[str, str]:
    """Where each robot holds: its node from TeamState (`at`), the end node of the edge it is on
    ("a->b" -> b), or, with no position, its spawn node."""
    out = {}
    for r in team:
        at = positions.get(r, "")
        if TRAVEL_SEP in at:
            at = at.split(TRAVEL_SEP, 1)[1]
        out[r] = at or spawn_nodes[r]
    return out


def fallback_plan(team: Sequence[str], positions: Mapping[str, str], spawn_nodes: Mapping[str, str],
                  reason: str = R.PLANNING_EXHAUSTED) -> TeamPlan:
    return hold_all_plan(team, hold_targets(team, positions, spawn_nodes), reason)
