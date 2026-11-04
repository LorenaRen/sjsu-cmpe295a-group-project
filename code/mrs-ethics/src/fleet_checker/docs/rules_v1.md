# Shared Checker — Rules v1 (Sprint 1)

Status: draft v0.9, 2026-11-04

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

The sweep counts robots, not claims.  A robot whose consecutive claims on
a resource overlap (margin > 0) or touch, to 1e-6 s (margin = 0), is there
throughout, so a conflict is not reported in two pieces split at that
robot's handover from one step to the next.  Between different robots the
intervals stay half-open: one leaving at t and another arriving at t is a
clean handover.

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
measured, and neither comes from a standard.  The gate's
`config/gate.yaml` sets R = 1.5 m and N = 1; N = 1 because with two
robots, N ≥ 2 can never be exceeded.

**Where people come from.**  The checker takes the people with every
check, as `HumanObservation`s (id, position in the map frame), the way it
takes the robots' start nodes.  Neither `narrow_aisle` nor the test map
`samples_world` has people, and the map format has no place for them (both
mrs_sim's loader and `sim_map.py` reject unknown keys).  People belong to a
scenario rather than to a world: `PerceivedEntity.msg` describes them as
actors from the scenario YAML.  Tests place people in code.  The gate
reads them from a people file (`people_file`; by default
`config/people.yaml`, which lists nobody, so S-2 finds nothing) and refuses
two people with one id, or a person outside every zone of the map (most
likely mistyped coordinates).  Later the positions are to come from
`/mrs/team_state` (see the limitations).

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
- **Source:** Project-defined.  Related standards: ISO 13482 (personal care
  robots; not yet reviewed; clause TBD); BS 8611 (ethical design of robots;
  not yet reviewed; clause TBD).

### S-4  Node occupancy

Two robots are at the same node at the same time, standing there or
driving through it.  A node is a point, and two robots cannot be at one
point.  A robot standing at a node blocks everyone who has to drive through
it, and two robots driving through a junction at once collide there.  This
is a vertex conflict with a duration, as in CBS; S-1's `EDGE_SWAP` is the
edge conflict.

**When a robot is at a node: while any part of it is over the node.**

| In the compiled plan | At the node |
|---|---|
| a `wait` task that has arrived and waits for its `depends_on` | from arrival until they are done |
| a `navigate` or `hold_position` task waiting for its `depends_on` before it leaves | from when it starts waiting until it leaves |
| the node the plan ends at, whatever the last task's action | from arrival to +inf (until the next plan) |
| a robot with no task | its start node, to +inf |
| driving through: a node on the way, the node where one task ends and the next drives on at once, or the start node it drives off from | for the time its body takes to drive over the node |

Counting only `wait` and `hold_position` by name would miss the second and
third rows.  The compiled times are the robot's middle.  Its front gets to
a node half of `robot_length / speed` earlier, and its back leaves half of
it later.  So driving through takes `robot_length / speed` (0.9 s at the
gate's placeholder values, 0.45 m and 0.5 m/s), and every stop is that
much longer too: a handover at a node needs the first robot's back to be
off the node when the second one's front gets there.  `margin` widens each
of these on both sides, as in S-1, for the error in the compiled times.
With `margin = 0` a robot driving through is still there for
`robot_length / speed`, never for no time.

`robot_length` is a required parameter (no default, like `speed`).  The
gate's `config/gate.yaml` holds a **placeholder**, 0.45 m: the chassis box
of mrs_sim's `mrs_bot.sdf` (0.45 × 0.30 m), not a measured robot.  A robot
that turns on a node sweeps more than its length; the margin covers that
for now.

**How it is checked.**  The compiler records, for each step, the node it
ends at (`PlanStep.end_node`) and every node it drives through or to, with
the time it gets there (`PlanStep.waypoints`; the first step starts with
the start node).  The second is needed because consecutive edges in one
zone are one step: a robot passes room_3 in the middle of its step from
east_n to corridor_1.  `plan.node_claims` turns both into claims, and
`cross_plan.check_node_occupancy` runs S-1's sweep on each node as a
resource of capacity 1.  R-1 chooses who yields, with the same entry
times as S-1 and S-2 (one ranking per round); by default, the robot that
gets there later.

**Nodes in a zone of capacity 1 are left to S-1**, which allows only one
robot in the whole zone anyway.  This also keeps a head-on swap from being
reported twice.  In both tested swaps the two robots drive through the
same nodes inside the narrow zone at once: narrow_aisle's swap has both at
aisle_mid at 14.4 s, and two_narrow_zones has both at w and at e, 1 s
apart.  S-1 reports them; S-4 does not look there.

Both rules can still report one encounter in one case: a node outside the
one-robot zone lies within `robot_length / 2 + 2 × margin × speed` of its
boundary (0.725 m at the gate's values), and the two robots' compiled
paths cross near it.  None of the maps has such a node; the closest are
1.0 m from the zone (corridor_1, wr, er).  If it happens there are two
findings with the same yielder (one ranking per round), and when they ask
for the same ordering it is sent once (see "One constraint per demand"
below).

**Who must change the plan.**  Usually R-1's yielder.  There is one case
where waiting cannot help: the keeper stays at the node to the end of its
plan, and the yielder has to be there after it, to drive through or
because one of its tasks ends there
(`node_presence.only_the_keeper_can_resolve`).  Then only the keeper can
free the node, so the keeper must fix the plan.  `Finding.must_fix` names
it, and so do `must_replan`, `conflicts()` and the constraint.
`Finding.r1_yielders` keeps R-1's own choice, from which the verdict
reads who yields and who keeps.

The ledger follows `must_fix`: the REJECT counts toward F-2 for the keeper,
and the approval records the yield for it (see "Updating the ledger").  It
is the robot that gives way.  Counting the rejection for R-1's yielder
instead would have a robot that was never asked to change anything build
up rejections, then jump the queue in other conflicts as "starving".

**Constraints** (`verdict.py`, one per yielder and keeper):

| Situation | Constraint | For |
|---|---|---|
| The keeper leaves later, and making the yielder's task that brings it to the node wait for the keeper's task that takes it away removes the conflict (checked again, as for S-1) | `order_before` | yielder |
| **The keeper stays to the end of its plan and the yielder drives through the node** | the keeper waits: `order_before`, the keeper's task that brings it to the node after the yielder's task that takes it through, if that works on the **whole plan** (below); else `forbid_node` for the keeper at the node, whose text says that this changes its final stop, because the yielder has to drive through, and why waiting does not help | keeper |
| The keeper stays to the end of its plan, and the node is where the yielder's task ends | `custom`: the yielder's task cannot be done with the plan as it is; end somewhere else | keeper |
| Ordering does not help, and the yielder drives through the node | `custom`, saying why waiting does not help | yielder |
| Ordering does not help, and the node is where the yielder's task ends | `custom`: wait at another node near it first, and go there only after the keeper has left.  `forbid_node` would tell the planner never to stop there, i.e. to give the task up | yielder |
| Otherwise: the yielder only waits there between tasks | `forbid_node` for the yielder at that node | yielder |
| The yielder is at the node from the start of its plan | `custom`: it has to leave before the keeper arrives | yielder |

**Why the keeper's wait is checked on the whole plan.**  Every other
`order_before` delays the yielder's getting to one place, and is verified
locally: is the conflict at that place, between those two, gone?  A new
conflict elsewhere is the next round's to catch.  The keeper's wait moves
all that is left of the keeper's plan later, so the two robots can now
meet anywhere.  So the reordered plan is checked whole: the conflict at
the node must be gone, and the keeper and the yielder may meet nowhere
they did not meet before.

The fake planner's `p1_keep_clear_stop` shows why.  In round 3 robot_b
parks at corridor_1, robot_a's only way to room_3.  If robot_b waits at
east_n until robot_a has gone by, the conflict at corridor_1 is gone.  But
robot_a then stays at room_3, and east_n's only way out is through room_3,
so the two would meet there.  The whole-plan check finds that, robot_b gets
`forbid_node`, and the fake planner parks it at corridor_2, a dead end
nobody drives through; round 4 is approved.  In `s4_park_after_pass`,
robot_a drives through corridor_1 to corridor_2.  There robot_b can wait,
and both keep their targets.

**With S-1 and S-2.**  In a round every conflict ranks robots by the same
key (F-2, yields, task type, first entry over all conflicts, robot id), so
every `order_before` the checker sends has a robot with a lower key wait
for one with a higher key: they cannot form a cycle among themselves.  The
exception is the keeper's wait above, which points the other way.  It is
checked on the plan as proposed, not together with the verdict's other
orderings, so it could close a cycle with them in a rare case.  None of the
tested scenarios has one.  The planner then cannot follow both (the fake
planner refuses the second as circular), and the next round sees what it
did.  `forbid_node` and `custom` are not orderings.  What a verification
does not see, a fix for one rule causing a conflict under another, the
next round catches on the REJECT path; MODIFY must check the whole plan
again.

**Known limitations.**

1. **Only the same node.**  Two robots at two different but close nodes
   are not reported; how far apart nodes are is up to the map.
2. **Only where the compiler puts a robot, at the nominal speed.**  Where
   the executor really is (when the CBF slows it down, say) is not
   modelled.
3. **Following and head-on traffic in open rooms.**  Two robots driving
   the same way through a node less than `robot_length / speed +
   2 × margin` apart (1.9 s, a 0.95 m gap between their middles, at the
   gate's values) are reported: conservative, as the margin is.  Two
   robots meeting head-on in a room are reported only if they meet within
   `robot_length / 2 + margin × speed` (0.475 m) of a node.  A meeting
   between two nodes of a room, which has no capacity, is the CBF's.
4. **A starving keeper still has to move.**  Which robot must fix a
   conflict only the keeper can resolve follows from who can resolve it,
   not from the ranking.  So F-2 does not keep a starving keeper at the
   node; it still protects it in other conflicts.
5. **The keeper's wait and the round's other orderings** are not checked
   together (see "With S-1 and S-2").

- **Scope:** cross-plan
- **Data:** each step's `end_node`, `waypoints`, action and times
  (`Plan`); `robot_length` and `speed`; `WorldMap.capacity()` (zones of
  capacity 1)
- **Source:** Project-defined.

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
- **Source:** Project-defined.  Related standard: ISO 3691-4 (driverless
  industrial trucks; not yet reviewed; clause TBD).

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

When S-1, S-2 or S-4 detects a conflict, the checker must decide **which**
robots replan (yield).  A conflict with *n* distinct robots in a region
of capacity *c* needs **n − c** of them to yield — e.g. three robots
entering a capacity-1 aisle send two back to replan.  One exception, in
S-4: when the keeper stays at the node to the end of its plan and the
yielder has to be there, only the keeper can free it, and the keeper
replans instead (see S-4).

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

The round includes **S-2's and S-4's conflicts as well as S-1's**
(`check_cross_plan_conflicts`): a robot's entry time is its earliest claim
start over all of them, whether that claim is on a region, on a person's
surroundings or on a node.  Ranked per rule, S-1 could have b wait for a in an aisle
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
- **Source:** Project-defined, inspired by Pozanco & Borrajo (2022).

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
- **Source:** Project-defined, inspired by Pozanco & Borrajo (2022).

### F-2  Starvation

A robot has had to give way in M consecutive REJECTs: R-1 told it to
yield, or, in S-4, it was the keeper of a node that only it could free (across
any conflict group; rejections for the robot's own P-1, P-2 or P-3
violations do not count).  When triggered, the starving robot receives **priority override**:
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
| REJECT, and the plan compiles | +1 for each robot that must give way in this round: R-1's yielders, or the keeper of an S-4 conflict only the keeper can resolve (S-4) | unchanged; each conflict and its yielders are remembered until the next APPROVE (a later round's version of the same conflict replaces the earlier one) |
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
| `compiler.py` | TeamPlan + map + start nodes → one timed `Plan` per robot.  Route = shortest path on the waypoint graph; time = distance ÷ nominal speed (a parameter, no default); order = each robot's task list plus `depends_on`.  Each step also gets its polyline (`PlanStep.path`: the nodes it reaches and the points where the route crosses a zone boundary, driven at constant speed; one point while the robot stands still), which S-2 measures distances from, the node it ends at (`PlanStep.end_node`; for a step that stands still, where it stands), and every node it drives through or to, with the time it gets there (`PlanStep.waypoints`), which S-4 needs.  Reports unknown robots, unknown targets, unreachable targets and dependency cycles, including cycles through task order, which Module 1's validation does not catch. |
| `team_check.py` | `check_team_plan`: compile → P-1/P-2/P-3 for each robot → S-1, S-2 (when `people` and a `CrowdingLimit` are given) and S-4 + R-1 across robots.  Each violation carries its `mrs_common.reasons` code, the TeamPlan task ids it came from and the robots that must fix it.  Restricted zones default to "no robot may enter".  `recheck(plan)` runs the same check, with the same people, on a changed plan.  `conflicts(result)` lists the S-1, S-2 and S-4 conflicts once each (both halves of a head-on swap are one), with where they are (zone, person or node) and their yielders, for the ledger. |
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
| S-2 `human_crowding` | The same, with "away from the person" for "out of the zone" and "near them" for "in": `order_before`, verified first, else `custom`.  The texts give R, N and the person's nearest node, because the planner knows nodes, not where people stand. |
| S-4 `node_occupied` | `order_before`, verified first; if ordering does not help, `custom` when the node is where the yielder's task ends or the yielder drives through it, else `forbid_node`.  A keeper staying to the end where the yielder has to be is the one to change: it waits for a yielder that drives through (checked on the whole plan), else `forbid_node`; `custom` when the yielder's task ends there.  See S-4. |
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

**One constraint per demand.**  Two rules can ask for the same change: at
the crossroads next to a person, S-2 and S-4 both have robot_a wait for
b1.  A constraint is sent once per demand (kind, robot, task, node, value;
for `custom`, its text), the first rule's; `rule_ids` still lists every
rule, and the rationale every problem.

### Verifying an `order_before`

An ordering is not always a fix.  The keeper may stay in the zone until
the end of its plan; the yielder may already be in the zone when its plan
starts; the ordering may close a cycle with the plan's `depends_on`; or the
plan may already contain it (the conflict then comes from the time
margin).  So each suggested `order_before` is added as a `depends_on` to a
copy of the TeamPlan, and the copy is checked again with the same map,
start nodes, people, ledger and settings.  If the conflict between those
two robots in that zone (for S-2: near that person) is gone, the
`order_before` is sent; otherwise a `custom` constraint says why ordering
them does not help.  For S-2 the reasons are the same, with "near the
person" for "in the zone".  S-4 falls back to `forbid_node`, or to
`custom` when the node is where the yielder's task ends (see S-4).

The verification looks only at **that pair of robots in that zone** (or
near that person, or at that node).  It
does not guarantee that the reordered plan creates no new conflict
elsewhere.  On the REJECT path the next round's check catches those;
MODIFY must check the whole modified plan again before approving it.

One ordering is checked on the whole plan instead: S-4's keeper waiting
for a yielder that drives through the node.  It moves all that is left of
the keeper's plan later, not just one robot's getting to one place, so the
two robots must meet nowhere they did not meet before (see S-4).

### Other fields

- **`rule_ids`**: the `mrs_common.reasons` codes of the findings, sorted.

  | Reason code | Rule |
  |-------------|------|
  | `capacity_exceeded`, `edge_swap` | S-1 |
  | `human_crowding` | S-2 |
  | `node_occupied` | S-4 |
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
  | 3 | 1.0 | S-1, S-2, S-4 | robots predicted to collide or block each other; too many robots near one person; two robots at one node.  S-2's level is a **placeholder**, ranked with S-1 until the team decides |
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
- **When.**  Only if every finding is S-1, S-2 or S-4 and each produced a verified
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
  speed is measured), `robot_length` (for S-4; no default either, and the
  file's 0.45 m is a placeholder), `margin`, `max_duration`, `starvation_threshold`,
  `policy_file`, and for S-2 `crowding_radius` (R) and
  `crowding_max_robots` (N), which have no default either
  (`config/gate.yaml` holds placeholders) and `people_file`.  The access
  policy and task types are a YAML file (`config/policy.yaml`) because ROS
  parameters cannot hold nested tables; the people are a YAML list
  (`config/people.yaml`, nobody), named by the launch argument
  `people_file` because they belong to the scenario.  The node logs the
  effective settings as one JSON line at start-up, and one JSON line per
  decision.
- **FALLBACK** (assumption, to confirm with Module 4): the all-hold plan
  (`hold_all_plan`, `source_verdict` FALLBACK) does not pass through the
  gate; Module 4 publishes it on `/mrs/validated_plan` itself.  The gate
  subscribes only to `/mrs/proposed_plan`, so it never sees one.
  - *Why Module 4.*  Only the planner counts attempts.  The gate judges
    each message on its own and does not know the cap; to know it, the
    gate would need P-4's replan history, which leaving P-4 to Module 4's
    cap avoids.  mrs_common is written this way: `hold_all_plan` is for
    "Module 4, after attempts are exhausted", and `planning_exhausted` is a
    planner code.
  - *Why not through the gate.*  The fallback is the last step: if the gate
    rejected it, nothing would be left to do.  And the gate might well
    reject it.  Holding where they are can break P-1 (a robot stopped in a
    doorway) or S-1 (two robots already inside a capacity-1 zone), but that
    is where the robots are, not a choice of plan, and rejecting it makes no
    robot safer.  Standing still starts no new motion between robots, and
    each robot's CBF still applies.  Not passing through the gate also
    keeps the fallback out of the ledger: it is not an APPROVE.
  - The fake planner (`mrs_ethical_gate/dev_nodes`, a development node that
    applies the gate's constraints without an LLM) does it this way:
    `hold_all_plan` for the whole fleet at its start nodes, reason
    `planning_exhausted`, carrying the attempt of the last REJECT, and a
    `planning_exhausted` EpisodeEvent with it.
  - *To align with Module 4:* whether Module 4 publishes the FALLBACK and
    the `planning_exhausted` event; which attempt the FALLBACK carries;
    what Module 4 does when the gate does not answer; and the attempt cap.
    **If the cap is 3 or less, F-2 (M = 3) never acts within one planning
    cycle**: a robot rejected 3 times in a row is put first only at the 4th
    check.  The count carries over to the episode's next planning cycle,
    since only an APPROVE resets it and the gate never sees the fallback.
  - *To align with Module 6:* `/mrs/validated_plan` then has two
    publishers, the gate (APPROVE) and Module 4 (FALLBACK).  It is transient
    local with depth 1, kept per publisher, so an executor that joins late
    gets the last message of each, in no set order.  It has to take the
    newest (by stamp, or by transaction_id and attempt), not the last to
    arrive.
  - The fallback holds each robot at its current node.  With `team_state`,
    a robot on an edge has none, as for the gate's start nodes (above).

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
| S-2 | Human crowding | safety | cross | implemented (checker and gate); people from a static file until `/mrs/team_state` |
| S-3 | Unsafe action near humans | safety | single | deferred |
| S-4 | Node occupancy | safety | cross | implemented (checker and gate): standing and driving through |
| P-1 | Final stop in passthrough zone | policy | single | implemented |
| P-2 | Region access restriction | policy | single | implemented |
| P-3 | Maximum plan duration | policy | single | implemented |
| P-4 | Replan divergence circuit-breaker | policy | cross | not yet; may be covered by Module 4's attempt cap (to confirm) |
| R-1 | Yield assignment | resolution | cross | implemented |
| F-1 | Yield imbalance metric | fairness | cross | ledger data only; metric not computed |
| F-2 | Starvation override | fairness | cross | implemented |
