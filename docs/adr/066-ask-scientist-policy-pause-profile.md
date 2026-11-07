# ADR-066: Ask-the-scientist policy — trigger classes + per-loop pause profile

**Status:** Accepted (policy + profile-mode gate landed)
**Date:** 2026-09-22 (gap row D-33, under maintainer proceed authority)
**Relates to:** ADR-036 (loop hosting + approvals inbox), ADR-064 (one driver), gap rows D-33 / D-34

## Context

The design pauses the loop for the scientist when a step is *costly* (large
cluster time, physical robot action), *ambiguous* (competing hypotheses) or
*consequential* (direction change), with pause frequency as a per-project
setting that falls as the catalog matures. The tree had exactly two modes:
`auto` (never parks) and `ask` (parks at every workflow trigger), and the
approval inbox parks the whole engine while one action is undecided (D-34's
branch-continuation leg is separate).

## Decision

1. **Trigger classes** (vocabulary over action kinds):
   `cost` (any `workflow_trigger` / `agent_iteration`), `competing_hypotheses`
   (emitted at hypothesis selection when ≥2 candidates are live).
   Reserved emitters, named but not yet produced anywhere:
   `physical_action` (mcp-trusted hardware calls, lands with D-24/D-49) and
   `direction_change` (goal/phase redirects, lands with branch scheduling).
2. **Pause profile**: loop start accepts `mode="profile"` +
   `pause_triggers: [class, ...]` (stored as `metadata.pause_profile`). The
   engine driver's gate parks only when the action's class is in the
   profile; `auto`/`ask` unchanged. A per-project default profile (persisted
   on the project record) is the follow-up when maturity stages land — the
   loop-level profile is the honest v1.
3. Unknown action kinds carry **no class** — profile mode lets them run
   (never a silent park).

## Consequences

- D-33 moves Absent → Partial: policy recorded, two classes live, two
  reserved emitters named. Maturity-stage frequency and the project-record
  default are the follow-ups.
- The gate's park/deny semantics are unchanged (durable inbox, cancel vs
  reject distinction from WP-07).
