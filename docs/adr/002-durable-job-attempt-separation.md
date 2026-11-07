# ADR-002: Job/Attempt Separation for Crash Recovery

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** INV-02 (execution authority), INV-03 (attempt isolation), INV-04 (no concurrent attempts)

## Context

UMRS runs durable jobs on HPC clusters where crashes (UMRS process, node, Slurm)
are expected. The system must recover gracefully without data corruption or
resource leaks.

A contributor might model jobs as a single mutable entity with status transitions,
which breaks crash recovery semantics.

## Decision

Jobs and Attempts are separate database entities:

- **Job** = user intent. Immutable spec, one row per logical job. Has a
  `commit_attempt_id` pointing to the current authoritative attempt.
- **Attempt** = one execution try. Multiple attempts per job on retries. Each
  attempt writes outputs to its own S3 prefix (`s3://…/jobs/<job_id>/attempt_<n>/…`).

### Crash Recovery Protocol

1. On UMRS restart, mark all in-flight attempts as `LOST`
2. Requeue parent jobs (if retries remain)
3. Create new attempt from scratch
4. **No backend cleanup** — rely on attempt output isolation

### Concurrency Control

PostgreSQL advisory lock (`pg_try_advisory_lock(hashtext(job_id))`) ensures at
most one active attempt per job across all UMRS instances.

## Consequences

- All job outputs are attempt-scoped: `s3://…/jobs/<job_id>/attempt_<n>/…`
- Only the `commit_attempt_id` outputs are read by consumers
- Failed attempt outputs remain in S3 (garbage collected later) but are invisible
- The `execution_authority` field (slurm|k8s|compose) is written once and never changes
- The reconciler uses `execution_authority` to query the correct backend for stale jobs

## Alternatives Considered

- **Single mutable job row**: Cannot safely track partial outputs from failed runs.
  A crash during status update leaves the job in an ambiguous state.
- **Filesystem-based state**: Not durable across node failures. PostgreSQL
  provides ACID guarantees and advisory locks.
