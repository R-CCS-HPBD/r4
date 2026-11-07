# ADR-002 Amendment (2026-09-07): per-attempt execution manifests + executor-backed reproduce

**Status:** Proposed (with implementation in WP-10 round 1, same PR)
- **Date:** 2026-09-07
- **Related:** ADR-002 (durable job/attempt separation), P7 two-tier ratified
  clause (P7a records by construction; P7b recorded results re-execute),
  ADR-041 (scope grammar), WP-10.

## Context

The r4-contracts provenance DTOs (`ExecutionManifest`, `EnvironmentSnapshot`,
`CodeVersionRef`) existed with **zero constructor call sites** — only
`ProvenanceRecord` was populated, and nothing at attempt granularity (G-P7-1).
Replay split in two: run-definition replay was already executor-backed
(`trigger_run_definition` calls `trigger_workflow` with the recorded params —
"exact same inputs"); step replay created a trace row with `status=created`
and never re-executed (G-P7-2's live remainder). GRANULAR step re-execution
(calling back into a step's entrypoint with snapshot inputs) requires a
step-type→executor mapping the trace store does not carry (no
entrypoint/config on trace rows) plus a production-faithful `StepContext`
(S3, buckets, metadata) — that is its own project.

## Decision

1. **Attempts end with a manifest.** `JobStore.mark_attempt_succeeded` and
   `mark_attempt_failed` assemble and persist an `ExecutionManifest`
   (provenance from `build_provenance` + `EnvironmentSnapshot` from runtime
   facts + `CodeVersionRef` from the running source tree) under
   `job_attempts.metrics['manifest']` — **no schema change** (the JSONB
   metrics column is the honest slot). Persistence is **best-effort by
   contract**: a manifest failure logs and never blocks the attempt's
   terminal transition. Read paths: `get_attempt_manifest`,
   `get_latest_attempt_manifest` (commit attempt preferred), routes
   `GET /umrs/jobs/{id}/manifest` and `/manifests` (403/404 semantics
   identical to job reads).
2. **Job-grain reproduce is the executor-backed replay.** `POST
   /umrs/jobs/{id}/reproduce` requeues the job's spec so the **durable
   runtime re-executes the same inputs**, and returns the **drift window**:
   the recorded attempt code SHA (from its manifest) vs the current tree's
   SHA — `pinned / same_code / recorded_code_sha / current_code_sha / note`,
   with `pinned=false` when the prior attempt carries no recorded code
   version (drift window declared as undeclared, never hidden). Access =
   job owner/project like cancel; not admin-only.
3. **Step-grain replay stays intent-space, said as such.** `step_replay`'s
   trace-row-only result is documented as intent registration: granular
   re-execution needs the step-type→executor mapping + `StepContext` faith
   (round-2+ project, named here instead of hidden).
4. **r4 surface:** `client.jobs.reproduce(job_id)` and `r4 job reproduce`
   print the drift window (the remedy verb spelled as `r4 attempt reproduce`
   in the gap — same CLI family home; the verb is reproduce).

## Consequences

- G-P7-1 → Partial: manifests now populated and persisted per attempt
  (environment + code + provenance); RAG/model pins and a versions table are
  the named round-2 legs.
- G-P7-2 → Partial: executor-backed replay exists at job grain with honest
  drift reporting; run-definition replay's existing real dispatch is
  recorded as the workflow-grain path; step-grain remains intent-space by
  declaration. Contradicted → Partial.

## Verification

- `umrs/tests/test_manifest_builders.py` (env/git/shape, 7), `test_manifest_store.py`
  (persist/read round-trip + best-effort-on-failure, 3), `test_manifest_routes.py`
  (manifest latest/list/404/403 + reproduce drift modes, 8).
- `r4/tests/test_job_reproduce.py` (client route + direct-mode route + all
  three drift print forms, 5).
