"""GateSession: one ProposedPlan in, one decision out (pure Python, no ROS).

The gate node hands every message to decide() and publishes what comes back:

  plan_json -> TeamPlan          unreadable: REJECT llm_malformed, nothing is checked
            -> where robots are  the start source (starts.py); a robot whose position is not
                                 known: REJECT unknown_robot, nothing is checked
            -> check_team_plan   with this episode's fairness ledger
            -> to_verdict        APPROVE or REJECT
            -> ledger update     (rules_v1.md, "Updating the ledger")

  REJECT that compiles   record_rejection for each robot R-1 told to yield. Each conflict is
                         remembered until the next APPROVE; a later round's version of the
                         same conflict replaces the earlier one.
  APPROVE                record_yield for each yielder of each remembered conflict, then
                         record_acceptance for every robot; nothing is remembered any more.
  anything else          compile error, unreadable plan, unknown positions, a message seen
                         before: no change.

A message is identified by (transaction_id, attempt): the planner reuses the transaction_id
for later attempts. A new episode_id starts from scratch (empty ledger, nothing remembered,
no messages seen), so that episodes stay independent trials.

S-2 checks around the people of the people file and, with start_source team_state, the people
the robots see: a person in both is where the robots see them. Everyone is taken to stand still
for the whole plan. S-2 conflicts update the ledger exactly as S-1's do. With team_state, routes
also go around the snapshot's blocked edges.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from mrs_common.plan_schema import TeamPlan, plan_from_json

from fleet_checker.adapters.sim_map import SimMap, load_sim_map
from fleet_checker.adapters.team_check import (NO_ENTRY_TO_RESTRICTED, Conflict, TeamCheckResult,
                                               check_team_plan, conflicts)
from fleet_checker.adapters.verdict import APPROVE, VerdictContent, to_verdict, unreadable_plan_verdict
from fleet_checker.cross_plan import FairnessLedger
from fleet_checker.crowding import CrowdingLimit
from fleet_checker.plan import HumanObservation, TaskType
from fleet_checker.policy import DEFAULT_MAX_DURATION, AccessPolicy

from fleet_checker.adapters.verdict import unknown_positions_verdict

from .settings import GateSettings, SettingsError
from .starts import TEAM_STATE, FixedStarts, Starts, TeamStateStarts, UnknownStarts, spawn_start_nodes


@dataclass(frozen=True)
class Decision:
    verdict: VerdictContent
    validated: TeamPlan | None          # to publish on /mrs/validated_plan; APPROVE only
    result: TeamCheckResult | None      # None when plan_json could not be read
    ledger: dict[str, dict[str, int]]   # per robot, after this decision
    replayed: bool = False              # this (transaction_id, attempt) was decided before
    new_episode: bool = False           # this message started a new episode
    starts: Starts | UnknownStarts | None = None   # where the robots were; None if not looked at
    people: tuple[tuple[HumanObservation, str], ...] = ()   # S-2's people, each with "file" or the
                                                             # robot whose report counted

    def summary(self) -> dict[str, Any]:
        """Plain JSON values, for the node's log line."""
        return {"verdict": self.verdict.verdict, "rule_ids": list(self.verdict.rule_ids),
                "must_replan": sorted(self.result.must_replan) if self.result else [],
                "replayed": self.replayed, "new_episode": self.new_episode, "ledger": self.ledger,
                "positions": self.starts.summary() if self.starts else None,
                "people": [{"id": p.human_id, "x": p.position[0], "y": p.position[1], "from": source}
                           for p, source in self.people]}


class GateSession:
    """starts: where the robots are, FixedStarts or TeamStateStarts (starts.py); a mapping of
    robot -> node is taken as FixedStarts. Either way it covers the whole fleet."""

    def __init__(self, sim_map: SimMap, starts: Mapping[str, str] | FixedStarts | TeamStateStarts, *,
                 speed: float,
                 robot_length: float, margin: float = 0.5, max_duration: float = DEFAULT_MAX_DURATION,
                 access_policy: AccessPolicy = NO_ENTRY_TO_RESTRICTED,
                 task_types: Mapping[str, TaskType] | None = None, starvation_threshold: int = 3,
                 people: Sequence[HumanObservation] = (), crowding: CrowdingLimit | None = None):
        if people and crowding is None:
            raise ValueError("people were given without a CrowdingLimit: S-2 needs R and N")
        if isinstance(starts, TeamStateStarts) and crowding is None:
            raise ValueError("team_state without a CrowdingLimit: the robots may see people, and S-2 needs R and N")
        self.sim_map = sim_map
        self.starts = FixedStarts(starts) if isinstance(starts, Mapping) else starts
        self.people = tuple(people)    # the people file's
        self._check_args = dict(speed=speed, robot_length=robot_length, margin=margin,
                                max_duration=max_duration,
                                access_policy=access_policy, task_types=task_types, crowding=crowding)
        self.starvation_threshold = starvation_threshold
        self._start_episode(None)

    @classmethod
    def from_settings(cls, settings: GateSettings) -> GateSession:
        """Raises SettingsError for a person outside every zone of the map: most likely a typo
        in the coordinates, which would otherwise mean S-2 never sees that person."""
        sim_map = load_sim_map(settings.map_path)
        for person in settings.people:
            if not sim_map.zones_at(*person.position):
                x, y = person.position
                raise SettingsError(f"people_file {settings.people_file}: {person.human_id} at "
                                    f"({x:g}, {y:g}) is outside every zone of map {sim_map.name!r}")
        if settings.start_source == TEAM_STATE:
            starts = TeamStateStarts(sim_map, settings.robots, max_age=settings.team_state_max_age)
        else:
            starts = FixedStarts(spawn_start_nodes(sim_map, settings.robots))
        return cls(sim_map, starts, speed=settings.speed,
                   robot_length=settings.robot_length, margin=settings.margin,
                   max_duration=settings.max_duration,
                   access_policy=settings.access_policy, task_types=settings.task_types,
                   starvation_threshold=settings.starvation_threshold,
                   people=settings.people, crowding=settings.crowding)

    def decide(self, plan_json: str, *, transaction_id: str, episode_id: str, attempt: int,
               now: float) -> Decision:
        """The verdict for one ProposedPlan. `now` [s], on the gate's clock, timestamps the yields
        recorded and tells how old the robots' positions are (start_source team_state)."""
        new_episode = episode_id != self.episode_id
        if new_episode:
            self._start_episode(episode_id)
        key = (transaction_id, attempt)
        if key in self._seen:
            return replace(self._seen[key], replayed=True, new_episode=False, ledger=self.ledger_summary())

        try:
            plan = plan_from_json(plan_json)
        except ValueError as e:   # pydantic's ValidationError is a ValueError
            decision = Decision(unreadable_plan_verdict(e), None, None, self.ledger_summary(),
                                new_episode=new_episode)
        else:
            decision = self._check(plan, transaction_id, episode_id, attempt, now, new_episode)
        self._seen[key] = decision
        return decision

    def _check(self, plan: TeamPlan, transaction_id: str, episode_id: str, attempt: int, now: float,
               new_episode: bool) -> Decision:
        starts = self.starts.at(episode_id, now)
        if isinstance(starts, UnknownStarts):   # fail closed: a robot left out could be in the way
            return Decision(unknown_positions_verdict(starts.problems), None, None, self.ledger_summary(),
                            new_episode=new_episode, starts=starts)
        people = self._people(starts)
        result = check_team_plan(plan, self.sim_map, starts.start_nodes, on_edges=starts.on_edges,
                                 blocked_edges=starts.blocked_edges, people=[p for p, _ in people],
                                 ledger=self.ledger, plan_id=transaction_id, revision=attempt,
                                 **self._check_args)
        verdict = to_verdict(result)
        self._update_ledger(result, verdict, now)
        result = replace(result, memo=None)   # what the verdict's rechecks shared; kept decisions need none
        return Decision(verdict, plan if verdict.verdict == APPROVE else None, result, self.ledger_summary(),
                        new_episode=new_episode, starts=starts, people=people)

    def _people(self, starts: Starts) -> tuple[tuple[HumanObservation, str], ...]:
        """The people file's and the people the robots see, by id; for an id in both, where the
        robots see them, which is newer than the file."""
        people = {p.human_id: (p, "file") for p in self.people}
        people.update({p.human_id: (p, robot) for robot, p in starts.people_seen})
        return tuple(people[i] for i in sorted(people))

    def ledger_summary(self) -> dict[str, dict[str, int]]:
        return {robot: {"yields": self.ledger.yield_count(robot),
                        "rejections_in_a_row": self.ledger.consecutive_rejections.get(robot, 0)}
                for robot in self.starts.fleet}

    def _start_episode(self, episode_id: str | None) -> None:
        self.episode_id = episode_id
        self.ledger = FairnessLedger(starvation_threshold=self.starvation_threshold)
        self._pending: dict[str, Conflict] = {}       # conflict key -> latest version, until APPROVE
        self._seen: dict[tuple[str, int], Decision] = {}

    def _update_ledger(self, result: TeamCheckResult, verdict: VerdictContent, now: float) -> None:
        if result.compiled.violations:
            return   # a plan that does not compile says nothing about who gave way
        if verdict.verdict == APPROVE:
            for c in self._pending.values():
                for robot in c.yielders:
                    self.ledger.record_yield(robot, f"{self.episode_id}/{c.key}", now, ",".join(c.keepers))
            for robot in result.compiled.plans:
                self.ledger.record_acceptance(robot)
            self._pending.clear()
            return
        found = conflicts(result)
        for c in found:
            self._pending[c.key] = c
        for robot in sorted({r for c in found for r in c.yielders}):
            self.ledger.record_rejection(robot)
