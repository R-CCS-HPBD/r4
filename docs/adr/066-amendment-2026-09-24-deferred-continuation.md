# ADR-066 Amendment: Deferred continuation (D-34 first leg)

**Status:** Accepted (2026-09-24, maintainer proceed authority)
**Amends:** [ADR-066](066-ask-scientist-policy-pause-profile.md) — ask-the-scientist pause profile (D-33); gap row D-34 (first leg)

## Context

ADR-066 made the engine gate **park** the driver thread on a pending
named-class trigger: while a question sits in the durable inbox, the
whole loop stalls — including work that has nothing to do with the
question. The design (suggestion 9, D-34) asks for the opposite shape:
the loop continues on independent work while a branch-specific question
pends.

## Decision

**In PROFILE mode, a PENDING named-class trigger DEFERS instead of
parking (first leg of D-34).** The gated action is skipped, the
iteration is finalised honestly as `awaiting_decision` (no experiment
ran, no findings minted — nothing fabricated), the durable question
stays in the approval inbox, and the loop continues to its next
iteration. The next iteration re-requests idempotently under the same
approval id and proceeds as soon as the decision lands (approved → the
action runs; rejected → the existing denied path).

**Bounded by a 3-strike rule:** after 3 consecutive deferrals under the
SAME approval id the gate hard-parks as before — the human is clearly
away, and a pending question must never spin the loop's iteration
budget away. A different approval id resets the ledger.

**Ask mode keeps hard parking** (the operator asked to gate EVERY
action; deferral would spin iterations without work).

## Consequences

- A profile-mode loop with a pending question makes progress on
  ungated iterations instead of stalling — the first honest form of
  "continue on independent branches".
- Iteration records gain the `awaiting_decision` status (recorded with
  the deferral label in `results.deferred_gate`; replay renders it).
- The deferral ledger (`loop.metadata.deferred_gate`) is durable across
  restarts — a resumed loop inherits its strike count.
- Full parallel-branch scheduling (multiple simultaneous hypothesis
  tracks) stays a named later leg of D-34; this amendment is the
  continuation half only.

## Verification pointers

- `umrs/src/umrs/knowledge/engine_loop_runner.py` — `_gate` deferral
  protocol, `_peek_decision`, `_defer_strike` (3-strike ledger)
- `umrs/src/umrs/knowledge/loop.py` — `_gate_deferred` (hypothesis +
  workflow trigger sites), `awaiting_decision` status
- `umrs/src/umrs/knowledge/agentic_engine.py` — deferred agent iteration
- `umrs/tests/test_deferred_continuation.py` — 9 pins
