# ADR-060: Deployment topology — one UMRS hub per institution, cluster-scoped execution targets

**Status:** Accepted (design direction; convergence is multi-wave)
**Date:** 2026-09-22 (maintainer decision, design reset wave)
**Relates to:** ADR-059 (transport/edge), ADR-018 (reverse-SSH venues), ADR-046 (remote sessions), migration 0088 (claim-side cluster scoping), gap rows D-47 / D-48 / D-49 in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Context

The design states: one UMRS server on the institution's front-end is the hub;
several clusters connect to it; CPU-only dedicated servers host agent sessions
when the front-end saturates; trusted dedicated servers host hardware-bound
MCP servers.

The deployed reality diverges: umrs-front (data0) runs an **independent UMRS
stack per cluster** (rccs and ai4s live; bdrt and octopus profiles), each with
its own gateway ports, Postgres and object store
(`deploy/ansible/inventory/umrs-front/`, `umrs/deploy/umrs-front/`). A shared
jobs table already needed claim-side cluster scoping (migration 0088) after
cross-cluster job theft. The venue grammar (`frontend`, `cluster:<name>`)
already names execution targets across stacks. CPU-only and trusted-host
venue classes do not exist anywhere (D-48, D-49 Absent).

The alternative on the table was amending the design instead: declare
per-cluster stacks the permanent model.

## Decision

**The design stands: one UMRS hub per institution.** Per-cluster stacks are
the *transitional* topology, not the model.

1. **One hub, cluster-scoped targets.** A single UMRS (edge process + server,
   ADR-059) on the front-end owns accounts, the job registry, object storage,
   the catalog and the MCP registry. Clusters are execution targets addressed
   by the existing `venue` / `cluster:<name>` grammar — no new addressing
   scheme.
2. **Per-cluster stacks are transitional.** The rccs / ai4s stacks keep
   serving until the hub absorbs them; convergence migrates each cluster's
   jobs/knowledge/catalog into the hub registry behind the migration-0088
   claim scoping, then retires the per-cluster Postgres and gateway ports.
   No big-bang cutover; each cluster converges when its lane is proven.
3. **New venue classes land on the hub's lease rail** (ADR-046 Amendment 2):
   `cpu:<host>` for remote agent sessions on CPU-only dedicated servers
   (D-48) and a trusted-host venue for hardware-bound MCP servers (D-49),
   with placement and trust-class fields in the MCP registry (D-19/D-21).
   Hardware access never leaves its trusted host; agents reach it through
   UMRS, never directly from a sandbox.
4. The object-store fleet cutover (ADR-055, SeaweedFS) proceeds per-converge
   as today; hub convergence does not gate it.

## Consequences

- Gap row D-47 stays **Contradicts** (tree ≠ design) but now has a decided
  direction and this ADR as its closing instrument; D-48/D-49 stay Absent
  with the venue classes named here. ROADMAP phase 0 topology row closes.
- What already exists is the seed, not the obstacle: migration 0088 claim
  scoping, the `venue` column, `cluster:<name>` grammar, ADR-018 tunnels and
  the ADR-046 lease rail are exactly the pieces the hub model needs.
- Deployment/Ansible changes (hub registry absorb, per-cluster retirement)
  are their own later waves; no converges are triggered by this ADR.
- Every "which stack owns this?" question defaults to the hub; per-cluster
  stacks may not grow new registry planes.

## Rollout-status (2026-10-03)

- **U-1 landed repo-side** (wave of 2026-10-03, plan:
  docs/development/umrs-hub-convergence-plan-2026-10-03.md):
  the endpoint registry — the last registry plane still split per cluster —
  moves to the shared `umrs_db` (migration 0117 + `venue` column,
  jobs-style scoping; `UMRS_ENDPOINT_REGISTRY_SHARED=1` on all four servers;
  idempotent copy from the four `front_*` tables at each converge). Verified
  context same day: durable jobs, knowledge, projects, groups, quota,
  models, catalogs, grants, buckets, access lists (ADR-080), workflow
  catalog state and the object store were already shared; what remains
  per-site is U-2 accounts PG-first, U-3 one server process, U-4 one
  gateway pair (named legs in the plan).
- **2026-10-03, U-1 follow-ups** (code review; repo-side, needs converge):
  the copy now runs once per venue (migration 0118 markers; the four fronts
  are sealed; an error fails the converge), and umrs-rag reads the hub table
  read-only, scoped to `rccs-front`. U-1 shares storage, not visibility:
  each server and RAG see only their venue's rows. Details are in the plan,
  section "U-1 follow-ups".
- **2026-10-03, review corrections:** the U-1 flip window was unprotected
  (the boot reconciler only finishes provisions already in the registry;
  the owner checks for reaped endpoints before the 0118 converge). The
  per-cluster rollback is lossy, because `front_*` is frozen at the flip.
  Open item: the hub table's primary key is `job_id` alone, while Slurm
  numbers jobs per cluster.
