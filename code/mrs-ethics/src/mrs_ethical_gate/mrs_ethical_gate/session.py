"""GateSession: one ProposedPlan in, one decision out (pure Python, no ROS).

The gate node hands every message to decide() and publishes what comes back:

  plan_json -> TeamPlan          unreadable: REJECT llm_malformed, nothing is checked
            -> check_team_plan   with this episode's fairness ledger
            -> to_verdict        APPROVE or REJECT
            -> ledger update     (rules_v1.md, "Updating the ledger")

  REJECT that compiles   record_rejection for each robot R-1 told to yield. Each conflict is
                         remembered until the next APPROVE; a later round's version of the
                         same conflict replaces the earlier one.
  APPROVE                record_yield for each yielder of each remembered conflict, then
                         record_acceptance for every robot; nothing is remembered any more.
  anything else          compile error, unreadable plan, a message seen before: no change.

A message is identified by (transaction_id, attempt): the planner reuses the transaction_id
for later attempts. A new episode_id starts from scratch (empty ledger, nothing remembered,
no messages seen), so that episodes stay independent trials.

S-2 checks around the people of the people file, taken to stand still where listed; when
Module 3's /mrs/team_state exists, their live positions will come from there instead. S-2
conflicts update the ledger exactly as S-1's do.
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

from .settings import GateSettings, SettingsError
from .starts import spawn_start_nodes


@dataclass(frozen=True)
class Decision:
    verdict: VerdictContent
    validated: TeamPlan | None          # to publish on /mrs/validated_plan; APPROVE only
    result: TeamCheckResult | None      # None when plan_json could not be read
    ledger: dict[str, dict[str, int]]   # per robot, after this decision
    replayed: bool = False              # this (transaction_id, attempt) was decided before
    new_episode: bool = False           # this message started a new episode

    def summary(self) -> dict[str, Any]:
        """Plain JSON values, for the node's log line."""
        return {"verdict": self.verdict.verdict, "rule_ids": list(self.verdict.rule_ids),
                "must_replan": sorted(self.result.must_replan) if self.result else [],
                "replayed": self.replayed, "new_episode": self.new_episode, "ledger": self.ledger}


class GateSession:
    def __init__(self, sim_map: SimMap, start_nodes: Mapping[str, str], *, speed: float,
                 margin: float = 0.5, max_duration: float = DEFAULT_MAX_DURATION,
                 access_policy: AccessPolicy = NO_ENTRY_TO_RESTRICTED,
                 task_types: Mapping[str, TaskType] | None = None, starvation_threshold: int = 3,
                 people: Sequence[HumanObservation] = (), crowding: CrowdingLimit | None = None):
        if people and crowding is None:
            raise ValueError("people were given without a CrowdingLimit: S-2 needs R and N")
        self.sim_map = sim_map
        self.start_nodes = dict(start_nodes)    # the whole fleet
        self._check_args = dict(speed=speed, margin=margin, max_duration=max_duration,
                                access_policy=access_policy, task_types=task_types,
                                people=tuple(people), crowding=crowding)
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
        return cls(sim_map, spawn_start_nodes(sim_map, settings.robots), speed=settings.speed,
                   margin=settings.margin, max_duration=settings.max_duration,
                   access_policy=settings.access_policy, task_types=settings.task_types,
                   starvation_threshold=settings.starvation_threshold,
                   people=settings.people, crowding=settings.crowding)

    def decide(self, plan_json: str, *, transaction_id: str, episode_id: str, attempt: int,
               now: float) -> Decision:
        """The verdict for one ProposedPlan. `now` [s] only timestamps the yields recorded."""
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
            result = check_team_plan(plan, self.sim_map, self.start_nodes, ledger=self.ledger,
                                     plan_id=transaction_id, revision=attempt, **self._check_args)
            verdict = to_verdict(result)
            self._update_ledger(result, verdict, now)
            decision = Decision(verdict, plan if verdict.verdict == APPROVE else None, result,
                                self.ledger_summary(), new_episode=new_episode)
        self._seen[key] = decision
        return decision

    def ledger_summary(self) -> dict[str, dict[str, int]]:
        return {robot: {"yields": self.ledger.yield_count(robot),
                        "rejections_in_a_row": self.ledger.consecutive_rejections.get(robot, 0)}
                for robot in sorted(self.start_nodes)}

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
