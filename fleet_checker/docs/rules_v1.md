# Shared Checker — Rules v1 (Sprint 1)

Status: draft v0.2, 2026-10-06

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
- **Source:** ISO 3691-4 (industrial truck safety clearance, clause TBD);
  project-defined

### S-2  Human crowding

At a given moment, more than N robots are within radius R metres of the
same observed human.  Each robot individually keeps a safe distance, but
their combined presence violates the limit — this is the cross-plan case
that per-robot guardrails cannot catch.

- **Scope:** cross-plan
- **Data:** `Claim` (which robot is in which region and when);
  `WorldMap.regions[].center` (approximate robot position);
  `WorldSnapshot.humans[].position`
- **Source:** ISO 13482 (personal-care robot safety distance, clause TBD);
  BS 8611 (ethical risk assessment, clause TBD)

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

A robot's plan has been rejected M consecutive times (across any conflict
group).  When triggered, the starving robot receives **priority override**:
it is kept ahead of every non-starving robot in the conflict, and the
n − c yielding robots are drawn from the non-starving robots first,
ranked by R-1 (fewer cumulative yields yields; `task_type` tie-break).

If a conflict contains more starving robots than the region's capacity,
starving robots are still kept ahead of non-starving ones, but some of
them must yield; among the starving robots, R-1 decides who yields.

Implementation note: R-1 and F-2 share one ranking key,
`(is_starving, yield_count, task_type rank)`, so the starvation branch
can never order robots in the opposite direction from R-1.

- **Data:** `FairnessLedger` consecutive-rejection count
- **Source:** project-defined (task-completion guarantee)

---

## Data structures needed (not yet implemented)

| Item | Used by | Notes |
|------|---------|-------|
| `FairnessLedger` | R-1, F-1, F-2, P-4 | Fourth data category alongside Plan / WorldMap / WorldSnapshot.  Accumulates across planning rounds; not a snapshot. |
| Policy config (access rules) | P-2 | Separate from WorldMap.  Maps `region_id \| region_kind` → allowed `TaskType` set. |
| Replan history | P-4 | Tracks `(conflict_group_id, round, outcome)`.  Could live inside the checker orchestrator rather than a standalone type. |

---

## Rule index

| Code | Name | Cat. | Scope | Impl. status |
|------|------|------|-------|:---:|
| S-1 | Interval capacity / edge-swap | safety | cross | Sprint 2 |
| S-2 | Human crowding | safety | cross | Sprint 2 |
| S-3 | Unsafe action near humans | safety | single | deferred |
| P-1 | Final stop in passthrough zone | policy | single | Sprint 2 |
| P-2 | Region access restriction | policy | single | Sprint 2 |
| P-3 | Maximum plan duration | policy | single | Sprint 2 |
| P-4 | Replan divergence circuit-breaker | policy | cross | Sprint 3 |
| R-1 | Yield assignment | resolution | cross | Sprint 2 |
| F-1 | Yield imbalance metric | fairness | cross | Sprint 2 |
| F-2 | Starvation override | fairness | cross | Sprint 3 |
