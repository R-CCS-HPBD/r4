# ADR-066 Amendment 2: Parallel hypothesis tracks (D-34 second leg)

**Status:** Accepted (2026-09-24, maintainer proceed authority)
**Amends:** [ADR-066](066-ask-scientist-policy-pause-profile.md) and
[Amendment 1 — deferred continuation](066-amendment-2026-09-24-deferred-continuation.md);
gap row D-34

## Context

Amendment 1 let a profile-mode loop skip an iteration whose gated action
waits on the scientist and continue with the next one. The loop still
pursues one hypothesis per iteration chosen afresh, so "continue on
independent branches" (design suggestion 9) only holds by accident: the
next iteration may well pick the same blocked branch again. Two shared
slots make a real multi-branch loop impossible today:

- `loop.metadata["awaiting_approval"]` is ONE approval id — a second branch's
  gated action would reuse the first branch's pending question;
- the deferral ledger resets whenever the approval id changes, so interleaved
  branches would reset each other's 3-strike count.

## Decision

1. **Opt-in `max_tracks` at loop start** (1–4, default 1). At 1 the loop is
   exactly the pre-amendment loop (no behaviour change for existing callers).
2. **Opening.** On the first hypothesis phase of a loop with `max_tracks > 1`
   and ≥2 plan hypotheses, the loop opens one track per distinct top-ranked
   hypothesis (existing evidence-overlap ranking; plan order in simulated
   mode), up to `max_tracks`. Tracks are durable in `loop.metadata["tracks"]`
   (`track_id`, `hypothesis_id`, `hypothesis`, `turns`, `status`).
3. **The competing-hypotheses question is asked only when there is a
   choice.** Tracks covering every candidate need no choice; with more
   candidates than tracks, the ADR-066 `competing_hypotheses` trigger is
   raised once, at opening (deferral/denial semantics unchanged).
4. **Scheduling.** Each iteration serves the next ACTIVE track round-robin;
   the iteration carries `results.track_id` and the track's hypothesis (no
   re-selection); each track walks the plan's experiment sequence with its
   own turn counter.
5. **Per-track gating.** Gated actions carry `track_id`; the awaiting-approval
   slot and the 3-strike deferral ledger are keyed per track, so a question
   pending on one track never blocks, reuses or resets another's.
6. **Rejection closes a track, not the loop.** A decided "no" on a track's
   gated action finalises the iteration as `track_rejected` and closes the
   track; the loop continues on the others and completes when none remain
   active. Cancellation/stop semantics are unchanged.
7. Budgets (iterations, wall-clock, GPU-hours) stay loop-wide.

## Consequences

- A profile-mode loop with a pending question on one branch keeps
  experimenting on the others — the full form of suggestion 9.
- Findings carry the track's `hypothesis_id` (unchanged field), so the
  catalog pins and publication builder see which branch produced what.
- Replay/status surfaces read `metadata.tracks` and `results.track_id`.
- Not in this amendment: direction-change detection across tracks (D-33's
  reserved `direction_change` emitter) and track merging/pruning by evidence.

## Verification pointers

- `umrs/src/umrs/knowledge/loop_tracks.py` — opening, scheduling, closing
- `umrs/src/umrs/knowledge/loop.py` — track-aware hypothesis phase + denial
- `umrs/src/umrs/knowledge/engine_loop_runner.py` — per-track approval slot
  and deferral ledger
- `umrs/src/umrs/api/loop.py` — `max_tracks` on loop start
- `umrs/tests/test_loop_tracks.py`
