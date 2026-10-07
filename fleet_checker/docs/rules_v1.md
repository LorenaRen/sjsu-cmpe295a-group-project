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

A plan whose last step is in an `AISLE` or `DOORWAY` region.  With
`hold_final=True` the robot claims that region to +∞, blocking other
traffic indefinitely.

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

The policy config shape (TBD) will map `(region_id | region_kind)` →
`set[TaskType]` (allowed) or a deny-list, loaded at checker start.

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
robot replans (yields).

Resolution order:
1. The robot with **fewer** cumulative yields (from `FairnessLedger`)
   yields this time.
2. Tie-break: `task_type` rank — `SERVICE` yields to `DELIVERY`
   (configurable).

The yielding robot receives the `Violation` with a structured rejection
reason; the other robot's plan is accepted.

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
its current plan is accepted and the conflicting robots are asked to
replan, regardless of R-1's normal yield assignment.

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
