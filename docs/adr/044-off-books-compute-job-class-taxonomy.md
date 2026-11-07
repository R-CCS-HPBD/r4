# ADR-044 — Off-books compute: job-class taxonomy + request-driven durability

**Status:** Accepted (vision tier landed 2026-09-07, WP-09 round 1)
**Date:** 2026-09-07
**Relates to:** ADR-002 (durable job/attempt), ADR-028 (companion turn/attempt),
ADR-036 (loop hosting), ADR-034 (A2L conservation join), G-P4-2 (WP-09).
**Principles:** P4 (deployment reality), P1 (invariants honest by default).

## Context

Vision inference calls, local companion chat turns, RAG queries, and MCP
invocations ran as **off-books compute**: observable only in logs, absent from
the durable job fabric, invisible to `job list/status/cancel`, and unreachable
by the WP-10 reproducibility chain (attempt manifests) and WP-11 attempt
credentials. Chat has partial attribution via `llm_usage_log` (migration 0050)
— attribution rows, not scheduler objects.

The unifying question is **which invocation classes ratchet a durable job and
which classes keep attribution rows**. Ratcheting everything makes the job
table a log table (bloat + cancel/retry semantics that must never fire);
attributing everything makes audit blind for long/cancellable compute.

## Decision

### The taxonomy (three lanes)

| Lane | Meaning | Termination contract | Examples |
|---|---|---|---|
| **Executor-driven durable job** | Scheduler owns attempts, requeue-on-crash, cluster/backoff policy | terminal via executor; restart → LOST + requeue or resolve | index builds, knowledge ingest/embed, workflows, endpoint provision/upgrade, model download, research loops |
| **Request-driven durable job** (NEW, this ADR) | The HTTP handler owns the one attempt; no executor, `RestartPolicy.NEVER`, `max_attempts=1` | admission on submit; terminal on downstream outcome/status poll/cancel; restart → LOST → job FAILED (never zombie-RUNNING) | **vision inference calls (this wave)** |
| **Attribution row only** | Sub-second or read-side work; cancel/retry semantics would be lies | row written, no JobType | `llm_usage_log` chat completions; RAG retrieval queries; MCP tool *invocations* (audit lane covers egress/capability) |

### Class table (current)

| Invocation class | Lane | Status after round 1 |
|---|---|---|
| Vision inference (`/vision/infer/*`) | **Request-driven durable job** (`VISION_INFER`) | LANDED |
| LLM chat/completions via endpoint | Attribution row (`llm_usage_log`) | landed (0050) |
| Companion chat turns | Executor-adjacent durable turns (ADR-028) + attribution | landed |
| Local companion tool turns vs venue workers | Durable worker per venue (COMPANION_WORKER) | landed (T3a) |
| RAG query/retrieval calls | Attribution row only | unchanged |
| MCP tool invocation | Audit lane; job only when an invocation itself spawns compute (e.g. workload submission) | unchanged |
| Batch inference / fine-tune / model-eval | Executor-driven durable jobs when they exist | **future executors** (INDEX_BUILD / WORKFLOW_RUN family patterns; scheduled separately, venue decided by the site) |

### Vision wrap mechanics (round 1)

- `VisionInferSubmit` mints `create_job(VISION_INFER)` +
  `create_attempt(backend="vision_endpoint")` + `mark_attempt_running`.
- Submit-response carries additive `job_id` (response model additive field).
- Status/get/cancel accept additive optional `job_id`: terminal transition on
  terminal poll (`completed`→succeeded, `failed`→failed,
  `cancelled`→cancel_job) or explicit cancel. Idempotent across repeated
  polls (`InvalidTransitionError` swallowed).
- All wrapping **best-effort**: absent durable pool ⇒ additive no-op
  (`job_id: None`); transition faults are logged and never break the call.
- `spec` records endpoint resolution + input/output refs (no blob content).

### Why request-driven, not executor-driven (this round)

- The downstream (BentoML vision service) already owns queueing/lifecycle of
  the task itself; UMRS-second scheduling would duplicate control planes.
- REST submit/status/cancel maps exactly onto job states without an
  in-process executor.
- NEVER means crash-reconciler marks stranded attempts LOST and FAILS the
  job — literally the truthful answer for a request-scoped call.

## Consequences

- Vision calls appear in `job list/status/cancel`, gain attempt manifests
  (WP-10 hooks apply best-effort) and are eligible for attempt-scoped
  credentials (WP-11 wiring is a later consumer).
- The taxonomy's rule for future classes: *if a scheduler can't honestly
  own retries, it's request-driven; if retention can't carry meaning, it's
  an attribution row.*
- **No TTL/TTL-relevant changes, no cancellation-kill guarantees** —
  `cancel_job` stops scheduling only; it never kills the backend task
  (documented in the job store API).
- Open legs (named, not hidden): `job_id`-keyed cross-source lookup tool for
  ops; batch-inference/fine-tune/model-eval executors (venue from the site);
  RAG/MCP attribution-row *cost accounting* (tokens/bytes if sites want
  them); TTL-castle/kill-capable cancel semantics needing backend control
  planes.
