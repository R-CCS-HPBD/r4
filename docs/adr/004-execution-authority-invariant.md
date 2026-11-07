# ADR-004: Execution Authority Immutability

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** INV-02 (execution authority immutability)

## Context

Each job is executed on exactly one backend: Slurm, Kubernetes, or Docker
Compose. The `execution_authority` field records which backend owns the job.

A contributor might add logic to migrate a job between backends (e.g., retry a
failed Slurm job on Kubernetes). This would break crash recovery.

## Decision

The `execution_authority` field is written once at job creation and **never
modified**. There is no `update_authority()` method in `JobStore`.

### Why

The reconciler (crash recovery) uses `execution_authority` to know which
backend to query:

```python
for job in stale_jobs:
    backend = get_backend(job.execution_authority)  # slurm|k8s|compose
    status = backend.check_status(job.attempt_id)
    if status == "lost":
        job.mark_lost()
        job.requeue()
```

If `execution_authority` could change between retries, the reconciler would
query the wrong backend for in-flight attempts.

## Consequences

- A job started on Slurm stays on Slurm for all retry attempts
- Cross-backend migration requires creating a new job (new job ID, new attempts)
- The hybrid orchestrator (`ExecutionTarget` × `RuntimeWrapper`) composes the
  backend at job creation time, not at retry time
- `JobStore.create()` writes the field; no mutation API exists

## Alternatives Considered

- **Mutable authority with attempt-level tracking**: Adds complexity and creates
  a state where the reconciler must check multiple backends per job. The
  simplicity of "one backend per job" outweighs the flexibility of cross-backend
  retry.
