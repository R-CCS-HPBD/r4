# ADR-069: Identity mapping to Slurm / S3 / k8s — per-target decisions of record

**Status:** Accepted (records the de-facto answers the tree already implements; gap row D-55)
**Date:** 2026-09-23
**Relates to:** ADR-043 (attempt-scoped STS), ADR-022 (credential broker), D-55, D-06 (accounts; the 0110 PG lane)

## Context

The design asks how R4/UMRS identities map onto the three infrastructure
planes that have their own identity systems: the scheduler (Slurm), object
storage (S3), and container orchestration (k8s). The tree already answers
each de-facto; this ADR records the decisions so the answers are policy, not
archaeology.

## Decision

### Slurm — per-user account attribution, two delegation modes

**Decision: impersonation-lite — per-user `--account=` attribution with an
optional per-user SSH delegation lane; the default is the UMRS service
connection carrying the requesting user's identity.**

- Every sbatch UMRS submits carries `--account=<slurm_account>` resolved
  per-user (`UserRecord.slurm_account`, with per-site overrides in
  `slurm_accounts` keyed by site name) — the scheduler's own accounting
  attributes usage to the user's cluster account, never to a shared
  platform identity.
- **Service-account mode (default):** the SSH ControlMaster connection is
  the UMRS service identity; `requesting_user` rides the job spec and the
  sacct attribution, so the platform's audit can join scheduler rows back
  to the user.
- **Per-user-SSH mode (`slurm_delegation: per_user_ssh`, site opt-in):**
  submission and cancellation execute as the requesting user via the UMRS
  service identity key with a per-user proxy — the scheduler sees the
  user's own SSH identity. The service key never becomes a user's key.

The audit trail: both modes stamp `requesting_user` (job spec + sbatch
exports), so `sacct` joins and platform joins agree.

### S3 — no user identities at all; attempt-scoped credentials

**Decision: no per-user S3 identities exist on compute. Storage writes
ride UMRS; where a workload must touch S3 directly, it receives
attempt-scoped STS credentials (ADR-043) minted at claim, dying with the
attempt — impersonation is never the model.**

- Client storage goes through UMRS (`r4 files`, the ZeroMQ FILE/OBJECT
  lane); per-user s3cfg delivery is stopped (D-16, `R4_WRITE_S3CFG=1`
  transition window only).
- Attempt-scoped STS (WP-11) + attempt presign: a job's spec of identity
  is the attempt; credentials are minted at claim and expire with it.

### k8s — service account, no user impersonation

**Decision: the platform's k8s lane runs under a service account;
user identity rides the durable job rows (project/user fields), not the
pod's service account.** The hybrid k8s scheduler submits jobs under the
platform service account; attribution joins on the job record's
`project_id`/`user_id` — the same join-not-copy rule the A2L domain uses.

## Consequences

- D-55's "record the decision per target" is closed: three targets, three
  recorded decisions, all matching shipped behavior.
- The D-06 accounts lane (0110) is the identity source these mappings read
  from on the platform side; the PG-first lane keeps the joins current.
- A per-user *database* identity does not exist and is not implied —
  Postgres is server-internal (single-host fabric, D-05's boundary).

## Verification pointers

- Slurm: `umrs/orchestrator/remote_slurm_backend.py` (`_delegation_mode`,
  `requesting_user`, `--account=` resolution via `UserRecord.resolve_slurm_account`);
  `umrs/admin/user_store.py` (per-user `slurm_account(s)`).
- S3: ADR-043; `umrs/jobs/attempt_credentials.py` (WP-11 STS);
  D-16 rows (s3cfg stopped).
- k8s: `umrs/orchestrator/hybrid/k8s_slurm_scheduler.py` (service-account
  submission; job rows carry the identity).

## Rollout-status (2026-11-08): Slurm decision replaced by ADR-112 D-1 and D-2

- **Replaced.** The Slurm decision above (the service-connection default and the `slurm_delegation: per_user_ssh` mode) is replaced by [ADR-112](112-user-hpc-accounts-gated-delegation.md) D-1 and D-2. The text above stays as the record of what was decided on 2026-09-23.
- **Why.** The shared per-user-SSH keys were destroyed on 2026-10-24. Since then every per-user attempt has fallen back to the service connection with only a warning, so the fallback, not a decision, chose whose login ran each job.
- **Wave A** of the gated-delegation plan:
  - deletes the fallback to the service connection; a lane that cannot be served under its resolved identity is refused with a typed `hpcaccess.*` code and never retried under another identity;
  - each site declares `scheduler.hpc_access`: the modes it allows, its platform login, whether that login belongs to a person, the operator's consent, and the interim project allow-list;
  - platform lanes (endpoints and serving, relay, tunnels, janitor, reaper, health) are pinned to the platform login; user-work lanes (staged steps, `hpc_script`, cluster loops) resolve per person and verified project;
  - `slurm_delegation` stays readable but is ignored; a site file that still says `per_user_ssh` logs an error naming `hpc_access`.
- **Per-user identity** will come from ADR-112 `user_account` (gated delegation, waves B1 and B2), not from a service key with a per-user proxy.
- **Still standing.** Per-user `--account=` attribution and the `requesting_user` audit join are kept. The S3 and k8s sections of this ADR are unchanged.
- **Verification pointers.** The `_delegation_mode` pointer above describes the code before wave A.
- **Gap row.** D-55 is re-opened (Partial); D-68 tracks user-registered accounts.
