# ADR-067: Loop budgets — per-project caps, wall-clock enforced, GPU-hour recorded

**Status:** Accepted (caps table + wall-clock enforcement landed)
**Date:** 2026-09-22 (gap row D-36, under maintainer proceed authority)
**Relates to:** ADR-048 (wrapper attempt budgets), ADR-051 (projects registry), V-04 (client LoopBudget wall clock)

## Context

The design budgets the autonomous loop per project: GPU-hour and wall-clock
caps with hard stops and notification. The tree had iterations-only caps
server-side and a client `LoopBudget.max_wall_clock_hours` that was dead
until V-04 fixed it (2026-09-22). Per-project caps did not exist anywhere.

## Decision

1. **`project_budgets` table** (migration 0104): `max_gpu_hours`,
   `max_wall_clock_hours` per project (`0` = uncapped), owner-or-admin
   upsert at `PUT /umrs/projects/{id}/budget`, read at the matching GET.
   No orphan rows (project must exist); both fields default to 0.
2. **Wall-clock enforcement**: loop start loads the project's row into
   `metadata.max_wall_clock_hours`; the engine driver checks elapsed time at
   every iteration boundary and stops the loop with
   `state_reason = budget_exhausted:wall_clock` (an operator-visible,
   stoppable-anyway stop — same cooperative class as pause/cancel).
   A loop without a budget row is uncapped; an unreadable row fails open
   with a logged warning (never a start blocker).
3. **GPU-hour cap is recorded, not enforced (leg B)**: per-loop GPU usage is
   not yet attributable across shared clusters (no cost feedback from
   cluster jobs to the loop record). It is stored in
   `metadata.max_wall_clock_hours`'s sibling `max_gpu_hours` and enforced
   when job-cost accounting lands — never approximated silently.

## Consequences

- D-36 moves Absent → Partial: caps exist and one class is enforced; the
  GPU accounting feedback loop is the named follow-up.
- V-04's client-side fix and this server-side cap are consistent: both
  enforce wall clock at boundaries with a named reason.
