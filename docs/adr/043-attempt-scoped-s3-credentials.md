# ADR-043: Attempt-scoped short-lived S3 credentials — scoped presign + mint trail round 1, STS round 2

**Status:** Proposed (with implementation in WP-11 round 1, same PR)
- **Date:** 2026-09-07
- **Related:** ADR-002 + amendment (durable jobs, manifests), CONSTITUTION
  "internal-only presigned URLs" invariant, development.instructions.md
  "Presigned S3 URLs exist only so compute nodes can read S3 without
  credentials — never show them to end users" (P5), G-P5-3.

## Context

Every presigned URL derives from **ambient site credentials** with a flat
one-hour TTL, unbound to the job/attempt asking for it
(`gateway/sidecar/main.py` env creds; `s3_presign._get_presign_client`
recycles whatever the ambient client holds, cached per endpoint + access
key). `SessionToken`/STS does not exist anywhere in the codebase (G-P5-3
grep = 0). A compromised or misbehaving attempt can ask for any key in any
bucket, and nothing auditable ties a URL to an attempt.

## Decision

**Round 1 (this PR): attempt-keyed records + scope-enforced presign.**

1. **Mint trail.** `POST /umrs/jobs/{job}/attempts/{attempt}/credentials`
   mints (idempotent while live) an `AttemptCredentials` record: scope
   `jobs/<job_id>/attempt_<n>/` (the durable output layout), TTL (default
   900s, `UMRS_ATTEMPT_CRED_TTL_SEC` override), minted/expiry timestamps.
   It is persisted under `job_attempts.metrics['credentials']` — **never
   any key material** (nothing secret ever lands in metrics).
2. **Scope enforcement.** `POST .../presign` 403s keys outside the recorded
   prefixes or past expiry; TTL is bounded to the minted TTL (900s
   default — no more ambient one-hour flat); expired records deny.
3. **403/404/409 semantics** match the jobs surface (owner like cancel;
   missing attempt 404; presign before mint 409 — no silent minting).

**Honest round-1 boundary (stated, not masqueraded):** the signing party
is still the site's ambient credentials; what a stolen attempt now loses
is *arbitrary keyspace and duration*, not ambient identity itself.
Per-attempt identity needs STS federation (`AssumeRole` against the S3
venue) — **round 2**, its own decision recorded here instead of faked:
MinIO in pre-release still serves the ambient key.

## Consequences

- G-P5-3 → Partial: attempt-keyed scope + short TTL + auditable minting
  landed with pin tests; STS federation (and sidecar wiring to the attempt
  record when provisioning compute) remains round 2.
- Access verbs unchanged for existing presign routes; the new routes are
  additive under the existing `jobs` family.

## Verification

- `umrs/tests/test_attempt_credentials.py` (19): mint record shape/nothing
  secret, expiry math, scope layout convention, inside/outside/
  sibling-expiry leading-slash matrix, round-trip through the metrics
  dict; routes: mint persists, idempotent-while-live, 404 unknown attempt,
  403 outsider, 409 before mint, inside-scope returns bounded-TTL URL,
  outside-scope 403, PUT routes the put generator.

## Rollout-status (2026-09-11, round 2 executed)

- **STS federation landed** (umrs `b121e19f`): `assume_role_for_attempt`
  mints per-attempt identity via MinIO `AssumeRole` with an inline policy
  scoped to the attempt's recorded prefixes (`GetObject/PutObject/
  DeleteObject` + prefix-conditioned `ListBucket`), TTL capped at 3600s,
  session name `umrs-<job>-<n>`. Fail-closed on any venue fault — never an
  ambient fallback (a fallback would masquerade as per-attempt identity and
  has the exact shape of the incident this ADR exists to prevent). Route
  `POST /umrs/jobs/{id}/attempts/{aid}/credentials/sts` carries owner/admin
  gates, 409-without-record, 503-on-venue-fault. The token is returned to
  the HTTP caller only — never persisted, never logged; the round-1 mint
  record is the audit trail.
- **Client verb landed** (parent `5064d361`): `r4 job attempt-credentials
  [--sts]` + `r4 job attempt-presign` -- the STS token prints once to
  stdout, called out explicitly; --json everywhere.
- **Sidecar wiring stays OPEN — named TTL/longevity mismatch, not code:**
  STS session TTL ceiling is 3600s; a serving mini-RiVault runs days.
  Injecting the session at provision time without a renewal loop would
  brick the serving plane's S3 hooks on the hour. The honest design is a
  *renewal sidecar inside the mini-RiVault stack*: a tiny profile polls
  `/credentials/sts` on a TTL-fraction schedule, re-renders the shared
  `litellm_fs`/env mount, and the serving containers pick creds on read —
  never through provision-time env freeze. That is its own code wave
  (needs the mini-RiVault profile + a live venue pin), deliberately not
  masqueraded here. Until it lands, the serving plane keeps ambient creds
  (status quo; the ambient-identity risk stays named by this ADR) while
  bounded-lifetime flows (attempt-scoped presign, provisioning-time
  writes ≤ 3600s) use the STS identity end-to-end today.
- Tests: `umrs/tests/test_sts_federation.py` (8 pins) +
  `r4/tests/test_job_attempt_credentials.py` (6 pins).

## Rollout-status addendum (2026-09-14, ADR-055 venue change)

- The STS venue on umrs-front becomes **SeaweedFS 4.47** (ADR-055). The
  `RoleArn` is no longer hard-coded: `assume_role_for_attempt(role_arn=…)`
  defaults to the configured backend's convention (`S3Config.sts_role_arn`,
  overridable via `S3_STS_ROLE_ARN`) — MinIO's placeholder
  `arn:aws:iam:::umrs-attempt` or SeaweedFS's declared role
  `arn:aws:iam::role/umrs-attempt`. The inline session policy is unchanged.
- **Latent bug fixed on the way:** the lazily built STS client passed no
  `region_name`, so botocore raised `NoRegionError` before any request left
  the process — the live route could never have federated on any venue.
  It now uses `S3_REGION` (default `us-east-1`).
- **Verified on SeaweedFS:** in-scope get/put/presign allowed, out-of-scope
  get/put denied, trust policy pinned to the admin principal (an ordinary
  identity cannot assume the role).
- **Known gap:** SeaweedFS 4.47 did not honour the `s3:prefix` condition on
  `s3:ListBucket` in the session policy. The deployment therefore omits
  `ListBucket` from the role's base policy: sessions cannot enumerate keys
  at all (rather than enumerating outside their scope). Presign-by-key
  flows are unaffected. Tracked by
  `SEAWEEDFS_PROFILE.sts_list_prefix_condition_enforced = False`.
