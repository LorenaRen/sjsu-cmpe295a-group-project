# Shared Checker — Rules v1 (Sprint 1)

Status: draft v0.6, 2026-11-02

---

## Safety

### S-1  Interval capacity violation

Same region or resource occupied by more robots than its `capacity` allows
during any overlapping time window.

When the violation is specifically two robots swapping adjacent regions
head-on (A→B while B→A), the detector labels it `EDGE_SWAP` instead of
`CAPACITY_EXCEEDED`.  Both are caught by the same sweep-line pass —
`margin > 0` widens each claim so the swap surfaces as a brief capacity
overlap; the detector checks whether the two offending claims involve
adjacent regions with swapped directions and tags accordingly.

- **Scope:** cross-plan
- **Data:** `Claim.resource_id`, `Claim.start/end` (from each Plan via
  `claims()`); `WorldMap.capacity()`; `WorldMap.regions[].neighbors`
  (for EDGE_SWAP identification)
- **Source:** Project-defined.  Related standard: ISO 3691-4 (driverless
  industrial trucks; not yet reviewed; clause TBD).

### S-2  Human crowding

At some moment, more than **N** robots are within **R** metres of the same
person.  Each robot on its own may keep its distance; it is their combined
presence that breaks the limit, and no per-robot guardrail can see that.

**How it is checked.**  The surroundings of a person, the disc of radius R
around them, are treated as one more resource with capacity N.  S-2 is then
S-1's problem again: claims on a resource, swept for moments with more
distinct robots than the capacity (`cross_plan.sweep_over_capacity`), with
R-1 choosing who yields.  Only the claims are new (`crowding.py`):

- Every compiled step has a path (`PlanStep.path`) driven at constant speed.
  On one straight piece the robot's position is linear in time, so "within
  R" is a quadratic inequality in t, and its roots, cut to the piece's time
  span, say exactly when the robot is inside.  Nothing is sampled.  Example:
  driving from (−5, 0) to (5, 0) at 1 m/s past a person at (0, 1) with
  R = 2 m gives (t − 5)² + 1 ≤ 4, so t ∈ [5 − √3, 5 + √3] ≈ [3.27, 6.73] s.
- A robot standing still is inside or outside for its whole step.  The disc
  is closed, but touching its edge for an instant is not a stay.
- One claim for each stretch of a step spent inside; a robot that leaves and
  comes back within one step gets two.  The sweep counts robots, not claims.
- `margin` widens each claim on both sides, as in S-1: it absorbs the timing
  error of the compiled timeline.  R is what absorbs position error
  (`WorldState.at` can be 0.5 m off; the CBF moves robots off the straight
  line).
- A robot whose plan ends inside the disc stays there (`hold_final`), so a
  robot parked next to a person takes one of the N places for good.
- People are taken to stand where they were observed for the whole plan.

**R and N** are parameters with no default in the checker
(`CrowdingLimit`), and both are **placeholders**: neither has been
measured, and neither comes from a standard.

**Where people come from.**  The checker takes the people with every
check, as `HumanObservation`s (id, position in the map frame), the way it
takes the robots' start nodes.  Neither `narrow_aisle` nor the test map
`samples_world` has people, and the map format has no place for them (both
mrs_sim's loader and `sim_map.py` reject unknown keys).  People belong to a
scenario rather than to a world: `PerceivedEntity.msg` describes them as
actors from the scenario YAML.  Tests place people in code.

**What S-2 does not do.**  A single robot too close to a person is the job
of each robot's own safety layer: the previous team's per-robot gate (speed
set to zero when too close) and Module 6's CBF filter (people are obstacles
with a larger clearance, `PerceivedEntity.msg`).  With N ≥ 1, one robot near
a person never fails S-2, and S-3 stays deferred.  S-2 predicts from the
plan: if the timings or the people's positions differ at run time, it can
miss crowding that does happen.  How often more than N robots really were
near a person is for the evaluation to count (Module 9's violation
monitor, Module 11), not for S-2.

**Known limitations.**

1. **Straight-line distance, through walls.**  A robot on the other side of
   a wall counts as near the person.  This errs towards reporting.
2. **Once people come from `/mrs/team_state`, only people a robot sees are
   checked.**  `TeamState` has no list of everyone in the scenario; people
   appear in each robot's `WorldState.obstacles`, within its perception
   range.  S-2 checks the route ahead, so a person on it whom no robot sees
   yet is missed.  To raise with Modules 3 and 9: publish every person in
   the scenario.
3. **R has to agree with Module 6's CBF clearance for people.**  R should be
   at least that clearance; otherwise S-2 counts positions the CBF never
   lets a robot reach.  Module 6 has not set the clearance yet.

- **Scope:** cross-plan
- **Data:** each step's `path` and times (`Plan`); the people
  (`HumanObservation`); R and N (`CrowdingLimit`)
- **Source:** Project-defined.  Related standards: ISO 13482 (personal care
  robots; not yet reviewed; clause TBD); BS 8611 (ethical design of robots;
  not yet reviewed; clause TBD).  R and N do not come from either.

### S-3  Unsafe action near humans — DEFERRED

> Deferred to a later sprint.  Reason: this is a single-robot rule — if a
> robot moves too fast near a person, the previous team's per-robot gate
> (distance-based speed override in `gate_rules.json`) already enforces
> a reactive stop.  The shared checker adds value on *cross-robot* rules
> that per-robot guardrails cannot cover.  We will revisit when
> Shervan provides speed parameters for each `Action`.

- **Scope:** single-plan
- **Source:** ISO 13482 (speed/force limits, clause TBD); BS 8611

---

## Policy

### P-1  Final stop in passthrough zone

A plan whose last step is in an `AISLE`, `DOORWAY` or `KEEP_CLEAR`
region.  With `hold_final=True` the robot claims that region to +∞,
blocking other traffic indefinitely.  `KEEP_CLEAR` is where the map says
a robot must not stop (e.g. in front of a door), so it is held to the same
rule as aisles and doorways.

- **Scope:** single-plan
- **Data:** `Plan.steps[-1].region`; `WorldMap.regions[].kind`
- **Source:** project-defined (congestion prevention)

### P-2  Region access restriction

Certain `task_type` values are forbidden from entering certain regions
(e.g. a `DELIVERY` robot must not enter a patient-care area).

Access rules live in a **separate policy configuration**, not on the
`Region` model, because:
1. Rules may change by time-of-day or operational mode without redefining
   the map.
2. Different deployments share the same map but different policies.

The policy config is `AccessPolicy`, with two tables of allowed task
types: `by_id` (region id → `set[TaskType]`) and `by_kind` (`RegionKind` →
`set[TaskType]`).  A region missing from a table is not restricted by it;
a step must pass both tables, so neither can grant what the other denies.
The tables are separate because one dict keyed by "region id or kind
value" is ambiguous: `narrow_aisle` names its narrow zone `aisle`, which
is also the value of `RegionKind.AISLE`, so a rule for that one zone
would also apply to every aisle.

`RESTRICTED` is only a label that comes from the map; who may enter is
decided here (an empty set means nobody).

- **Scope:** single-plan
- **Data:** `Plan.task_type`; `PlanStep.region`; policy config (new)
- **Source:** ISO 3691-4 (restricted operating zones, clause TBD)

### P-3  Maximum plan duration

A plan spanning more than T_max seconds is rejected.  Prevents a single
robot from monopolising corridor or resource capacity with an
unreasonably long plan.

- **Scope:** single-plan
- **Data:** `Plan.t_start`, `Plan.t_end`
- **Source:** project-defined

### P-4  Replan divergence circuit-breaker

The same conflict group (identified by the set of robot_ids involved) has
gone through N rounds of replan–check–reject without converging.  When
triggered, the checker stops asking LLMs to replan and falls back to a
deterministic resolution: robots pass the contested region one at a time
in priority order (priority = task_type rank, then fewest cumulative
yields).

- **Scope:** cross-plan
- **Data:** replan history per conflict group (new — tracked by the
  checker orchestrator); `Plan.task_type`; `FairnessLedger`
- **Source:** project-defined (liveness guarantee)
- **Status:** not implemented.  **May be covered by Module 4's attempt
  cap** — after its last attempt the planner gives up with
  `planning_exhausted` and every robot holds (`hold_all_plan`, published
  as a FALLBACK ValidatedPlan).  To confirm with Module 4 whether the
  checker still needs its own circuit-breaker.

---

## Conflict resolution

### R-1  Yield assignment

When S-1 or S-2 detects a conflict, the checker must decide **which**
robots replan (yield).  A conflict with *n* distinct robots in a region
of capacity *c* needs **n − c** of them to yield — e.g. three robots
entering a capacity-1 aisle send two back to replan.

Resolution order (robots are ranked; the first n − c yield):
1. The robot with **fewer** cumulative yields (from `FairnessLedger`)
   yields this time.
2. Tie-break: `task_type` rank — `SERVICE` yields to `DELIVERY`
   (configurable).
3. Tie-break: the robot expected to reach a conflict region **later**
   yields; the one that gets there first keeps going.  Entry times are
   rounded to 1e-6 s so that floating-point noise does not decide.
4. Tie-break: `robot_id`, so the order is total.

**Entry time is taken once per robot per round**, not once per conflict:
it is the robot's earliest claim start over *all* conflicts found in that
round.  Every robot therefore has one global sort key for the round, and
the yield relations of a round cannot form a cycle.  Taken per conflict,
a head-on swap breaks this: it is reported as two conflicts (one in each
region), each robot is "first" in the region it starts in, so each half
would send the *other* robot back, and the two resulting orderings would
contradict each other.  With a single conflict, the two definitions agree.

The round includes **S-2's conflicts as well as S-1's**
(`check_cross_plan_conflicts`): a robot's entry time is its earliest claim
start over both, whether that claim is on a region or on a person's
surroundings.  Ranked per rule, S-1 could have b wait for a in an aisle
while S-2 has a wait for b next to a person, in the same round
(`test_crowding.py::test_s1_and_s2_rank_robots_by_the_same_entry_times`).
The cost: when people are near the routes, an S-1 tie can be decided
differently from a check without people.  For S-2 a person's surroundings
have capacity N, so n robots near one person send n − N back.

In the TeamPlan pipeline every robot has the same task type, so after the
ledger the entry time decides; `robot_id` decides only when both robots
reach the conflict at the same moment (e.g. the mirror-symmetric swap in
`narrow_aisle`).

Each yielding robot receives the `Violation` with a structured rejection
reason; the remaining *c* robots' plans are accepted.

**Known over-yield.**  *n* counts every robot in the conflict window, but
they are not necessarily all inside at the same moment.  In a chain where
r1 overlaps r2 and r2 overlaps r3 but r1 and r3 never meet, capacity 1
asks two robots to yield even though removing r2 alone would suffice.
This is deliberately conservative: the robots kept never exceed capacity,
so the conflict is always resolved, at the cost of one extra replan.
Computing the minimum yielding set is an interval-selection problem;
revisit if experiments show chained overlaps are common.

- **Scope:** cross-plan
- **Data:** `FairnessLedger` (cumulative yields per robot);
  `Plan.task_type`
- **Source:** project-defined; informed by Pozanco & Borrajo 2022
  (workload-based fairness)

---

## Fairness

### F-1  Yield imbalance — evaluation metric

Not a violation that rejects a plan.  Instead it is:
1. **A conflict-resolution input:** R-1 uses cumulative yield counts to
   decide who replans next.
2. **An evaluation metric:** the difference between the maximum and
   minimum cumulative yield counts across the fleet.  When
   `max_yields − min_yields > K`, the system is considered unfair.
   K is a tunable threshold (initial value TBD, set by experiment).

- **Data:** `FairnessLedger` — `yields: dict[str, list[YieldRecord]]`,
  each record: `conflict_id`, `timestamp`, `opponent_robot_id`
- **Source:** Pozanco & Borrajo 2022 (workload-based fairness)

### F-2  Starvation

R-1 has told a robot to yield in M consecutive REJECTs (across any
conflict group; rejections for the robot's own P-1, P-2 or P-3 violations
do not count).  When triggered, the starving robot receives **priority override**:
it is kept ahead of every non-starving robot in the conflict, and the
n − c yielding robots are drawn from the non-starving robots first,
ranked by R-1 (fewer cumulative yields yields; `task_type` tie-break).

If a conflict contains more starving robots than the region's capacity,
starving robots are still kept ahead of non-starving ones, but some of
them must yield; among the starving robots, R-1 decides who yields.

Implementation note: R-1 and F-2 share one ranking key,
`(is_starving, yield_count, task_type rank, −entry time, robot_id)`, so
the starvation branch can never order robots in the opposite direction
from R-1.

- **Data:** `FairnessLedger.consecutive_rejections` (see "Updating the
  ledger" below); M is `FairnessLedger.starvation_threshold`, default 3
- **Source:** project-defined (task-completion guarantee)

### Updating the ledger

Whatever runs the replan loop writes the ledger; `check_team_plan` only
reads it.  That is the Module 5 gate (`mrs_ethical_gate`).  The two counts
are written at different times:

| Event | Rejections in a row (F-2) | Yields (R-1, F-1) |
|---|---|---|
| REJECT, and the plan compiles | +1 for each robot R-1 told to yield in this round | unchanged; each conflict and its yielders are remembered until the next APPROVE (a later round's version of the same conflict replaces the earlier one) |
| APPROVE | reset to 0 for every robot | one yield for each yielder of each remembered conflict; then nothing is remembered |
| Compile error, unreadable plan, a message seen before | unchanged | unchanged |

A conflict is identified by its zone and the robots in it; the two halves
of a head-on swap are one conflict.  P-1, P-2 and P-3 rejections do not
count toward F-2: a robot whose own plan breaks a rule should not gain
priority in conflicts.  Each episode starts with an empty ledger, so that
episodes stay independent trials whose results do not depend on the order
they ran in.

**Why yields are not recorded on REJECT.**  When the planner fails to fix
a conflict, the same conflict comes back in the next round.  If the
yielder's count went up on the REJECT, R-1 would pick the other robot the
next time, and the ordering sent to the planner would flip on every round.
Run: the checker on `valid_two_robots` with its `depends_on` removed
(`samples_world`, speed 0.5 m/s), the planner proposing that same plan six
times in a row.  Counts are the ledger before each round.

| Round | Old rule: yield (and rejection) recorded on every REJECT | New rule: rejection on REJECT, yield on APPROVE |
|---|---|---|
| 1 | robot_b yields (yields a, b = 0, 0) | robot_b yields (rejections in a row a, b = 0, 0) |
| 2 | **robot_a** yields (0, 1) | robot_b yields (0, 1) |
| 3 | robot_b yields (1, 1) | robot_b yields (0, 2) |
| 4 | **robot_a** yields (1, 2) | robot_a yields (0, 3): F-2, robot_b is starving |
| 5 | robot_b yields (2, 2) | robot_a yields (1, 3) |
| 6 | **robot_a** yields (2, 3) | robot_a yields (2, 3) |

Under the old rule the planner was told "robot_b: start b2 only after
robot_a has finished a1" and "robot_a: start a1 only after robot_b has
finished b2" in alternate rounds; a planner that keeps earlier constraints
would end up holding both, which is a deadlock.  Under the new rule the
yielder changes only where F-2 is meant to change it, after M = 3
rejections in a row.  `mrs_ethical_gate/test/test_session.py`
(`test_six_rounds_of_the_same_conflict`) runs the new column through the
gate's session.

**Known limitation.**  The yield is credited to the robot R-1 told to
yield; the approved plan is not examined.  If the planner resolved the
conflict another way (rerouting the keeper, say), the wrong robot is
credited.  Inferring the yielder from the approved plan is possible
(remove a cross-robot `depends_on` and check again: if the pair then
conflicts, the waiting robot yielded), at the cost of extra rechecks on
every approval; revisit if experiments show that planners often ignore the
orderings.

---

## Running the rules on a Module 1 TeamPlan

`fleet_checker/adapters/` connects the rules to the rest of the stack.  It
changes neither Module 1's `TeamPlan` nor Module 2's `mrs_sim`; the
checker core (`plan.py`, `cross_plan.py`, `policy.py`) does not import it.

| File | Does |
|------|------|
| `sim_map.py` | mrs_sim map file → `WorldMap`.  One region per zone; capacity from the map (none given = no limit); zone type → `RegionKind` (`room` → `OPEN_AREA`, `narrow` → `AISLE`, `keep_clear` → `KEEP_CLEAR`, `restricted` → `RESTRICTED`; other types are rejected).  Every edge is cut where it crosses a zone boundary, so the distance driven in each zone is known; zone adjacency comes from those cuts.  Maps with overlapping zones, nodes on a boundary two zones share, or edges that leave every zone are rejected. |
| `compiler.py` | TeamPlan + map + start nodes → one timed `Plan` per robot.  Route = shortest path on the waypoint graph; time = distance ÷ nominal speed (a parameter, no default); order = each robot's task list plus `depends_on`.  Each step also gets its polyline (`PlanStep.path`: the nodes it reaches and the points where the route crosses a zone boundary, driven at constant speed; one point while the robot stands still), which S-2 measures distances from.  Reports unknown robots, unknown targets, unreachable targets and dependency cycles, including cycles through task order, which Module 1's validation does not catch. |
| `team_check.py` | `check_team_plan`: compile → P-1/P-2/P-3 for each robot → S-1 + R-1 across robots.  Each violation carries its `mrs_common.reasons` code, the TeamPlan task ids it came from and the robots that must fix it.  Restricted zones default to "no robot may enter".  `recheck(plan)` runs the same check on a changed plan.  `conflicts(result)` lists the S-1 conflicts once each (both halves of a head-on swap are one), with their yielders, for the ledger. |
| `verdict.py` | `to_verdict`: a `check_team_plan` result → the decision fields of an `EthicalVerdict` (see "Verdict for Module 5" below). |

`TeamPlan.priority` is kept for logs only; who yields is decided by R-1
and the fairness ledger.  TeamPlan has no task type, so every robot is
`DELIVERY` unless the caller says otherwise; R-1's task-type tie-break then
never decides, and ties fall to entry time, then robot_id.

### Assumptions to confirm with Module 4 (planner) and Module 6 (executor)

The checker checks the compiled timeline, so its verdicts hold only if the
executor behaves the same way.  These are the compiler's assumptions, not
agreed interfaces:

1. **Action semantics.**

   | Action | Leaves when | Done when |
   |--------|-------------|-----------|
   | `navigate` | previous task done and every `depends_on` done | it arrives |
   | `wait` | previous task done (does not wait for `depends_on`) | it has arrived and every `depends_on` is done |
   | `hold_position` | like `navigate` | it arrives; as the robot's last task, the robot stays there |

   A `hold_position` that is not the robot's last task acts like
   `navigate`.
2. **Same route.**  The executor drives the same shortest path on the
   waypoint graph, including how equal-length routes are decided
   (compare node names along the route).
3. **`depends_on` is enforced by events** (`TaskStatus` "done"), not by the
   compiled times.  The times are estimates: nominal speed, with no
   acceleration, turning on the spot or CBF slow-down.  S-1's `margin`
   absorbs that error.  Speed and margin are to be set from measurements in
   simulation; no accuracy figure is claimed yet.

---

## Verdict for Module 5 (`EthicalVerdict`)

`fleet_checker/adapters/verdict.py` (`to_verdict`) turns a `check_team_plan`
result into the decision fields of an `EthicalVerdict`: `verdict`,
`rule_ids`, `standard_refs`, `risk_score`, `rationale` and `constraints`.
The gate node adds the ids, latency and stamp.  Only APPROVE and REJECT
are produced; MODIFY is designed (below) but not built.

### Violations → constraints

| Finding | Constraint (`mrs_common.plan_schema.Constraint`) |
|---------|--------------------------------------------------|
| S-1 `capacity_exceeded`, `edge_swap` | `order_before`: the keeper's task that takes it out of the zone (`task_id`) must finish before the yielder's task that takes it in (`value`); `robot_id` is the yielder.  One per (yielder, keeper) pair, verified first (below). |
| P-1 `final_stop_in_passthrough` | `forbid_node` for every node of the zone |
| P-2 `region_access_denied` | `avoid_node` for every node of the zone; `custom` if the zone has no node |
| P-3 `max_duration_exceeded` | `custom`, with the plan's duration and the limit |
| Plan does not compile | `custom`, with the violation message |
| `plan_json` is not a TeamPlan (nothing could be checked) | `custom`, with the first validation error and where it is (`unreadable_plan_verdict`); rule id `llm_malformed` |

P-1 and P-2 name every node of the zone, not only the one in the plan:
otherwise the next plan can stop at, or route through, a neighbouring
node of the same zone and be rejected again.  A head-on swap is reported
once per zone; its `order_before` text leaves the zone out, so both halves
give the same constraint and the planner gets it once.

### Verifying an `order_before`

An ordering is not always a fix.  The keeper may stay in the zone until
the end of its plan; the yielder may already be in the zone when its plan
starts; the ordering may close a cycle with the plan's `depends_on`; or the
plan may already contain it (the conflict then comes from the time
margin).  So each suggested `order_before` is added as a `depends_on` to a
copy of the TeamPlan, and the copy is checked again with the same map,
start nodes, ledger and settings.  If the conflict between those two
robots in that zone is gone, the `order_before` is sent; otherwise a
`custom` constraint says why ordering them does not help.

The verification looks only at **that pair of robots in that zone**.  It
does not guarantee that the reordered plan creates no new conflict
elsewhere.  On the REJECT path the next round's check catches those;
MODIFY must check the whole modified plan again before approving it.

### Other fields

- **`rule_ids`**: the `mrs_common.reasons` codes of the findings, sorted.

  | Reason code | Rule |
  |-------------|------|
  | `capacity_exceeded`, `edge_swap` | S-1 |
  | `final_stop_in_passthrough` | P-1 |
  | `region_access_denied` | P-2 |
  | `max_duration_exceeded` | P-3 |
  | `unknown_robot`, `unknown_target`, `no_route`, `dependency_deadlock` | plan does not compile (no rule number) |
  | `llm_malformed` | `plan_json` is not a TeamPlan (no rule number) |

  **TODO (Module 1):** `llm_malformed` is the planner's code for "output
  still invalid after the one retry"; the gate reuses it because
  `mrs_common.reasons` has nothing closer, and this step does not change
  mrs_common.  Ask Module 1 for a code of its own (e.g. `plan_unreadable`),
  so that the analysis can tell the planner giving up from the gate
  rejecting a plan it could not read.

- **`risk_score`**: the worst finding's level divided by 3.

  | Level | `risk_score` | Findings | Meaning |
  |-------|--------------|----------|---------|
  | 3 | 1.0 | S-1 | robots predicted to collide or block each other |
  | 2 | 0.67 | P-1, P-2 | a rule about where robots may be is broken |
  | 1 | 0.33 | P-3, plan does not compile, `plan_json` unreadable | the plan cannot run as written, or runs too long |
  | 0 | 0.0 | approved | |

  **This is an ordinal level, not a probability.**  Only the order of the
  levels means anything; the values are spread evenly over 0–1 because
  `EthicalVerdict.risk_score` is a 0–1 field.  Evaluation must not use it
  as a probability (for example, averaging it or comparing it with
  observed violation rates).
- **`rationale`**: two sentences: "Plan rejected: <up to three problems>."
  and "Propose a new plan that follows the constraints."  The constraint
  texts carry the details.
- **`standard_refs`**: empty.  `EthicalVerdict` allows only clauses the
  team has actually read, and the standards' text has not been read yet.

### MODIFY: designed, not built

- **Switch.**  A `modify` flag on the gate's entry point (a parameter of
  the gate node), off by default.
- **When.**  Only if every finding is S-1 and each produced a verified
  `order_before`.  P-1, P-2, P-3 and compile errors always go back to the
  planner: where else to stop or route needs its judgment.
- **How.**  Add all of those orderings as `depends_on` to a copy of the
  TeamPlan at once, and check the **whole** copy again.  If it passes,
  publish MODIFY: the modified plan goes into `ValidatedPlan`
  (`source_verdict` MODIFY) and the inserted orderings into `constraints`,
  so the agents learn what changed.  If it fails, publish the REJECT it
  would have been.  One pass and no loop, so the latency stays bounded.
- **Ledger.**  A MODIFY resolves its conflicts at once, so it updates the
  ledger as an APPROVE does: one yield for each yielder, then every
  robot's rejection count back to 0 (see "Updating the ledger").
- **Why a switch.**  Evaluation can then compare "the LLM replans" with
  "the checker repairs" on the same scenarios (pass rate without
  replanning, LLM calls per episode).  Off by default keeps REJECT as the
  baseline.
- The verification above already builds and rechecks reordered plans, so
  MODIFY is mostly wiring.

---

## Module 5 gate (`mrs_ethical_gate`)

The ROS 2 package around the checker.  `gate_node.py` receives, calls
`GateSession.decide` and publishes; everything it decides is in
`session.py`, `settings.py` and `starts.py`, which import no ROS and are
tested in a plain venv.

| Topic | | QoS (`mrs_common.qos.qos_for`) | Content |
|---|---|---|---|
| `/mrs/proposed_plan` | in | reliable, volatile, depth 100 | every ProposedPlan |
| `/mrs/verdict` | out | reliable, volatile, depth 100 | exactly one EthicalVerdict per message, with the message's transaction_id, episode_id and attempt |
| `/mrs/validated_plan` | out | reliable, transient local, depth 1 | APPROVE only: the TeamPlan as received, `source_verdict` APPROVE |

- **Messages.**  A message is identified by (transaction_id, attempt):
  the planner reuses the transaction_id for later attempts.  A message
  seen before gets the same verdict (and ValidatedPlan) again and leaves
  the ledger alone.  `plan_json` that is not a TeamPlan gets a REJECT
  (`llm_malformed`, see the TODO above), never silence: the planner is
  waiting for an answer.
- **Episodes.**  A new episode_id starts a new ledger and forgets the
  messages seen ("Updating the ledger").  The episode_id of the plans is
  enough; the gate does not depend on `/mrs/episode_event`.
- **Start nodes.**  `start_source: spawns`: robot i of the fleet
  (`robots`, the same value as `sim.launch.py`'s) at the map's spawn slot
  i, the whole fleet even when a plan names only some robots.  Right only
  until the robots move.  `team_state` (Module 3's `/mrs/team_state`: one
  snapshot of every robot at the same time, plus `blocked_edges`) is not
  built yet.
- **A robot on an edge** (`WorldState.at` = `"a->b"`, direction of travel)
  is refused for now (`starts.node_from_at`).  Intended handling, to build
  together with `team_state`: assume the robot finishes the edge it is on
  (to confirm with Module 6), add a first step that covers the rest of the
  edge (the zones and lengths of its remaining pieces, from the robot's
  pose), then compile the plan from b.  Without that step the robot would
  be in no zone until it reaches b, and a conflict there would be missed.
  `at` naming a node is approximate too (within 0.5 m of it): up to
  0.5 m ÷ speed of timing error, which the margin has to cover.
- **Times.**  Plans are compiled from t0 = 0, i.e. relative to the
  check.  plan_id is `<transaction_id>/<robot>` and revision is the
  attempt, so a finding's refs point at one message.
- **latency_ms.**  Wall-clock time of `decide()` (`time.perf_counter`):
  reading, compiling and checking the plan and building the verdict,
  including the rechecks that verify an `order_before`.  Not the node
  clock, which is simulation time under `use_sim_time` and can pause or
  change rate; receiving and publishing are not included.
- **Parameters.**  ROS parameters, read once at start-up and read-only
  (changing speed or margin mid-episode would make verdicts
  incomparable): `world` / `map_file`, `robots`, `start_source`, `speed`
  (no default; `config/gate.yaml` holds a placeholder until Module 6's
  speed is measured), `margin`, `max_duration`, `starvation_threshold`,
  `policy_file`.  The access policy and task types are a YAML file
  (`config/policy.yaml`) because ROS parameters cannot hold nested
  tables.  The node logs the effective settings as one JSON line at
  start-up, and one JSON line per decision.
- **FALLBACK** (assumption, to confirm with Module 4): the all-hold plan
  (`hold_all_plan`, `source_verdict` FALLBACK) does not pass through the
  gate; Module 4 publishes it on `/mrs/validated_plan` itself.  The gate
  subscribes only to `/mrs/proposed_plan`, so it never sees one.

---

## Data structures

| Item | Used by | Status |
|------|---------|--------|
| `FairnessLedger` | R-1, F-1, F-2, P-4 | Implemented (`cross_plan.py`): yield records (written on approval) and consecutive-rejection counts (written on REJECT) per robot, and F-2's threshold M.  Accumulates across the planning rounds of one episode; not a snapshot.  Nothing computes the F-1 imbalance metric from it yet. |
| `AccessPolicy` | P-2 | Implemented (`policy.py`): `by_id` and `by_kind` tables, kept separate from `WorldMap`. |
| Replan history | P-4 | Not implemented.  Tracks `(conflict_group_id, round, outcome)`; could live inside the checker orchestrator rather than as a standalone type. |

---

## Rule index

| Code | Name | Cat. | Scope | Impl. status |
|------|------|------|-------|:---:|
| S-1 | Interval capacity / edge-swap | safety | cross | implemented |
| S-2 | Human crowding | safety | cross | detector built (`crowding.py`, `check_crowding`) and resolved by R-1 with S-1 (`check_cross_plan_conflicts`); not yet run by `check_team_plan` |
| S-3 | Unsafe action near humans | safety | single | deferred |
| P-1 | Final stop in passthrough zone | policy | single | implemented |
| P-2 | Region access restriction | policy | single | implemented |
| P-3 | Maximum plan duration | policy | single | implemented |
| P-4 | Replan divergence circuit-breaker | policy | cross | not yet; may be covered by Module 4's attempt cap (to confirm) |
| R-1 | Yield assignment | resolution | cross | implemented |
| F-1 | Yield imbalance metric | fairness | cross | ledger data only; metric not computed |
| F-2 | Starvation override | fairness | cross | implemented |
