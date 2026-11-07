# ADR-028: Companion turn semantics — turn as attempt, at worker-level durability

**Status:** Proposed
- **Date:** 2026-08-17
- **Related:** ADR-002 (durable job/attempt separation), ADR-025 (research session authority), wave-companion-t3-venue-design.md (Open Q1 = this resolution), GAP-COMP-2, S6 acceptance findings.

## Context

The venue-design wave left **Open Q1** explicitly unresolved: should a companion
turn become a durable attempt of the platform's job schema? The question feeds
three things at once:

1. **Web/browser reattach** (S6 acceptance row 5 already re-raises this):
   mid-turn refresh must resume display without duplicates or lost content.
2. **Worker crash semantics** (venue design §6): crash mid-turn currently =
   attempt LOST on the *worker* lifecycle, which says nothing about the turn's
   own replay/idempotency.
3. **Trace convergence notice**: per-turn tool/thinking/budget traces exist in
   the client store (S3) with zero platform identity — a client cannot ask
   the platform which attempt its trace belongs to.

Facts on the ground now:

- Companion chat sessions persist locally (`session_store.py`); turns exist
  only as in-memory roundtrips through the agentic loop with no job-layer
  bottom rest worth its name.
- The platform doctrine: Job/Attempt separation, attempt-scoped writes,
  commit-pointer on the Attempt, single execution-authority per job
  (ADR-002, development.instructions).
- `LoopBudget.max_iterations=20` caps the engine; per-turn LLM budgets
  guard-constraint at session level (`session_usage.py`).
- CompanionWorkerManager (T3a) already uses restart exhaustion on
  durable-company worker as restart semantics; token issuance refuses
  failed/draining workers.

## Decision

1. **Turn is an attempt-level concept, not a job type.** A chat "turn" does not
   become a `JobType` of its own (jobs would proliferate per human prompt and
   pollute the jobs table). Instead:

   - Every chat turn started through the venue worker is registered as a
     `COMPANION_TURN` **Attempt** on the companion_worker job itself
     (`job.spec.companion_worker` already spawns it).
   - The attempt gets `attempt_id` (platform-generated), `request_token`
     (client-generated idempotency key), `user_message_hash` (sha-256 of the
     user input), `state`, `created_at`, `updated_at`.

2. **Attempt-scoped writes, no partial writes.** The worker buffers the
   assistant's response internally and commits exactly-once to the transcript
   at turn completion; crash mid-turn ⇒ attempt marked `failed` (never
   `succeeded`), no half-response committed. Re-issue semantics: the client
   sends the same `request_token` on retry; the worker returns the committed
   attempt if it completed, or starts a new attempt for a failed one
   (idempotent re-issue — visible in the audit trail as `reattempt_of`).

3. **Trace plumbing via attempt identity.** Every canonical T1 envelope emitted
   during a turn carries the attempt id (enriched by the python emitters).
   Client reattach: the client asks `GET /gw/companion/turns/{attempt_id}` to
   rehydrate the trace — buffers are local to the worker and have an attempt
   id, making reattach semantics provable instead of improvised.

4. **Conversation/turns reattach policy (browser refresh):**
   - assistant turn completed before disconnect → transcript committed, the
     client fetches it by id on replay;
   - mid-turn disconnect → the attempt is in-flight on the worker; the client
     subscribes to `GET /gw/companion/turns/{attempt_id}/events` (SSE) and
     replays the partial stream; when the attempt later completes, the same
     GET resolves to the committed result. Duplicated tool progress is
     impossible because the trace replays, never re-executes, from the
     attempt buffer.
   - worker crash before completion → attempt flagged `failed`, client offers
     re-issue; turns never silently restart.

5. **No change to permission/audit semantics.** Approvals stayed
   per-request-token; pending approvals carry the attempt id so a re-attached
   browser can resume an approval flow without a second request (approval
   pending rows dedupe on attempt id). All decisions audited with actor, the
   attempt id, session/token owner, and request token together.

### Explicitly rejected

- **`JobType.COMPANION_TURN` per chat message** — pollutes the jobs table
  with one job per prompt and stretches *job lifecycle* beyond what it
  models. Per-job backoff/restart policies also map wrongly: a retry of a
  conversational prompt is not the same failure class as infra backoff.
- Client-only reattach without attempt ids — runs directly into the
  duplicated-display failure that deprecated the legacy thinking path's
  double render.

## Consequences

- `umrs/src/umrs/jobs/companion_worker.py` gains the turn-attempt registry
  model (T3b milestone m2) and `CompanionWorkerManager` gets
  `begin_turn_attempt(...)` / `complete_turn_attempt(...)` /
  `fail_turn_attempt(...)` — small additive methods, no schema discipline
  violations.
- Data model table (T3b m3 `0071_companion_turns`): `attempt_id` PK, `job_id`
  FK, `request_token` UNIQUE per worker, `user_message_hash`, state,
  committed response ref (nullable), audit Timestamps.
- Emitters (AcP & JSONL bridge) include `attempt_id` in every T1 envelope
  payload — additive, tolerant-decode preserves older clients.
- Reattach contract lands behind `GET /gw/companion/turns/{attempt_id}` +
  SSE. r4-web client surfaces implemented on top of it (T3b later milestone),
  never direct into the protocol.
- Idle-worker TTL stays eviction-oriented — expired worker ⇒ in-flight
  attempts flagged failed on manager cleanup, replay stops honestly.

## Open carry-over

- **Pending approval flow under reattach** — do approvals keyed by attempt id
  expire under the worker TTL? Proposed: yes, with a typed `approval_expired`
  error to the client; an aborted pending approval is never auto-picked up
  silently. Final rule anchored when the T3b surface build reaches approvals.
- Lineage to ADR-025 research sessions — a turn's relationship to a research
  session should be a linker (explicit `research_session_id` column optional),
  not implicit overlay.
