# ADR-055: Object-store backend abstraction; SeaweedFS replaces MinIO on umrs-front

**Status:** Proposed (implemented 2026-09-14 umrs-front; Amendment A 2026-09-16 — fleet-wide swap, cutovers ride per-site converges)
**Date:** 2026-09-14
**Relates to:** ADR-043 (attempt-scoped S3 credentials — STS role convention), ADR-011 (catalog blobs in S3), `production-release-plan-2026-09-13.md` §5 (unpinned `minio/minio:latest`), CONSTITUTION §6.2 (security boundary change → architecture review)

## Context

MinIO's community edition is over. The `minio/minio` repository was archived
on 2026-04-25 and reads "THIS REPOSITORY IS NO LONGER MAINTAINED"; the last
community release (`RELEASE.2025-10-15T17-29-55Z`) fixed a **session-policy
bypass in STS and service accounts** — the exact mechanism ADR-043 uses as
the per-attempt isolation boundary. Nothing after that date will ever be
patched. The licence (AGPLv3 since 2021) was never the problem for this
platform: MinIO runs unmodified, internal, behind the S3 API. The
maintenance stop is.

Every deployment profile ran `docker.io/minio/minio:latest` (17 compose/k8s
files, the login-node role downloads an unversioned binary), so nobody could
even prove which build was live. The platform's **data plane** was already
backend-neutral — plain boto3, path-style SigV4, presigned GET/PUT — but
four MinIO-specific couplings existed:

| Coupling | Where (pre-ADR) |
|---|---|
| Admin plane: 741 lines shelling out to `mc admin user/policy` | `umrs/shared/storage/minio.py`, called from `server/provisioning.py`, `server/maintenance.py`, `api/ops.py`, `shared/storage/s3.py` |
| STS `AssumeRole` with a hard-coded MinIO-style `RoleArn` | `umrs/jobs/sts_federation.py` |
| Health path `/minio/health/live` | 19 files: compose healthchecks, `start.sh`, r4 diagnostics, DR rehearsal |
| Metrics `/minio/v2/metrics/cluster`, `mc admin` in runbooks, console on :9001 | Prometheus templates, alerts, ops docs |

## Decision

1. **Abstract the backend, not the client library.** Everything keeps
   speaking S3 through boto3. A small admin-plane contract,
   `umrs.shared.storage.backends.ObjectStoreBackend`, covers only what
   differs per store: `create_user` (identity + baseline list policy + user
   s3cfg), `grant_bucket_access`, `delete_user`, `remove_bucket`. A
   `BackendProfile` records the operational facts (health paths, metrics
   path, STS `RoleArn` convention, IAM port). Selection is `S3_BACKEND`
   (`minio` default → every other profile is behaviourally unchanged); the
   HTTP `Server` header is a consistency assertion, never a silent override.
2. **Two adapters.** `backends/minio.py` wraps the historical `mc` helpers
   unchanged. `backends/seaweedfs.py` drives SeaweedFS's IAM HTTP API with
   boto3's `iam` client — no extra binary in the server image. One
   semantic difference is accepted: SeaweedFS issues access-key ids, so a
   user's `access_key` is no longer their username (the IAM user name is).
3. **SeaweedFS `4.47` (pinned tag) replaces MinIO on umrs-front.** No data
   is migrated — the maintainer confirmed the current store holds nothing
   worth keeping — so the cutover is a fresh `umrs_s3_data` volume; the old
   `umrs_minio_data` volume is left for manual removal. The S3 port stays
   `9000`, so presign endpoints, reverse tunnels and gateway configs are
   untouched. Other profiles (rccs, bdrt, octopus, HPC login nodes) stay on
   MinIO for now and are out of scope here.
4. **A conformance suite is the extension point.**
   `umrs/tests/test_s3_backend_conformance.py` replays the platform's real
   patterns (vision-path presigned PUT with signed Content-Type, presigned
   GET, metadata, multipart, bucket policy, admin plane through the adapter,
   ADR-043 STS session policy) against any live endpoint. Adding a backend
   later = one profile, one adapter, one compose service, green suite.

### SeaweedFS deployment shape (umrs-front)

- `weed server -s3 -s3.iam -iam -metricsPort=9327` in one container
  (`umrs-seaweedfs`); S3 on `:9000`, IAM API on `127.0.0.1:8111`, metrics on
  `:9327/metrics` (Prometheus job `seaweedfs`; alert `ObjectStoreDown`).
- Admin identity bootstrapped from `AWS_ACCESS_KEY_ID/SECRET` = `.env`
  `S3_ROOT_USER/S3_ROOT_PASSWORD` (the role migrates the legacy
  `MINIO_ROOT_*` names). SeaweedFS names that identity
  `admin-<first 8 chars of the access key>`.
- Per-user identities live in the filer store on `/data` and are managed
  through the IAM API by the umrs servers; they survive restarts.
- `start.sh` renders `seaweedfs/iam.json` from `.env`: STS signing key
  (`S3_STS_SIGNING_KEY`, generated once), role `umrs-attempt` whose base
  policy is object read/write/delete inside `S3_BUCKET` **without
  `s3:ListBucket`** (see Verification), trust policy pinned to the admin
  principal so ordinary identities cannot assume it.
- No web console. Admin inspection: `curl :9000/status`, the IAM API, or
  `podman exec umrs-seaweedfs weed shell`.

## Verification (local, SeaweedFS 4.47 image built 2026-09-14, boto3 1.43.93 — the platform's pin)

| Check | Result |
|---|---|
| Presigned PUT with signed Content-Type via credential-less urllib; wrong Content-Type | 200; 403 `SignatureDoesNotMatch` |
| Presigned PUT with `x-amz-meta-*`, HEAD round-trip; presigned GET hash-verified; TTL 86400; expired/tampered | all as expected |
| Multipart 10 MB, bucket policy, ListObjectsV2, `/status`, `/healthz`, `/metrics` on 9327 | OK |
| IAM API: create user, key, `PutUserPolicy` bucket scoping (union across policy names), revoke, `DeleteUser` cascade, restart persistence | OK |
| Static `-s3.config` identity file **plus** IAM API | **does not coexist** (IAM calls return `Unknown`) → env-bootstrapped admin chosen |
| STS `AssumeRole` with the ADR-043 inline policy: in-scope get/put/presign; out-of-scope get/put | allowed; `AccessDenied` |
| `s3:ListBucket` with `s3:prefix` condition in the session policy | **not enforced** (out-of-scope listing allowed) in 3 of 4 runs → ListBucket removed from the role's base policy; sessions cannot list at all |
| Trust policy `Principal: "*"` | ordinary IAM user could assume the role (escalation) → trust pinned to the admin ARN; escalation then denied |
| `umrs/tests/test_s3_backend_conformance.py` against the shipped configuration | 8 passed |

MinIO presign/STS behaviour is the status quo (no re-verification needed; the
MinIO adapter is the pre-existing code path).

## Consequences

- **Security posture:** the object store is maintained again (weekly
  releases, active STS/IAM fixes in 4.45–4.47). Cost: STS sessions on
  SeaweedFS lose in-scope listing until upstream honours the prefix
  condition; presign-by-key flows are unaffected. `SEAWEEDFS_PROFILE.
  sts_list_prefix_condition_enforced=False` records this for tests/docs.
- **Operations:** `mc mirror` backups work unchanged (plain S3); `mc admin`
  runbook steps are replaced by IAM-API calls; the console is gone. Root
  credential rotation renames the admin identity (`admin-<ak8>`), which
  `start.sh` re-pins in `iam.json` automatically; existing per-user
  identities are unaffected.
- **Rollback:** `-e umrs_front_rollback_to_umrs_sha=<pre-ADR umrs sha>`
  restores the MinIO compose; `.env` keeps `MINIO_ROOT_*`, and
  `umrs_minio_data` is untouched until an operator removes it.
- **Not done here (deliberately):** pinning MinIO on the other profiles to
  the final release, migrating them, removing `mc` from `Dockerfile.server`.
  Each is its own small change; see the release plan §5 row.

## Rollout-status

- **2026-09-14 — implemented repo-side.** umrs: `shared/storage/backends/`
  (profiles, registry, MinIO + SeaweedFS adapters), `shared/storage/
  user_s3cfg.py`, dispatch in `s3.py`/`provisioning.py`/`maintenance.py`/
  `ops.py`, backend-driven `RoleArn` in `sts_federation.py` + `S3Config`,
  umrs-front compose/start/stop/setup/observability, 24 unit pins +
  8-test conformance suite (green vs 4.47). Parent: umrs-front role
  (S3_ROOT_* migration, S3_* env lines, bring-up detection, health probe),
  group_vars, Prometheus/alerts/backup templates, `dr-rehearsal.sh`, ops
  docs. **Deferred:** the r4 CLI `check_s3` probe still asks
  `/minio/health/live` only — `r4/_cli/diagnostics.py` carries a
  pre-existing CCN-17 function that fails the changed-file complexity gate
  on any touch, so the backend-neutral probe lands with that file's
  complexity fix (STATE `OBJ-20260914-001` follow-ups).
- **2026-09-14 — merged to main (PR #330, `3be0c2cf`; umrs pin `128f8211`).**
  Converge `mode=check` on main (run 34819619550) validated the object-store
  tasks against the live host (container absent → full bring-up scheduled;
  `S3_ROOT_*` migration writes two lines). Check mode cannot run to the end:
  the pre-existing `login_node : Wait for auth sidecar /health` `uri` task has
  no check-mode guard (reached only now that `--skip-tags r4_web` gets past
  the runner's missing r4-web build).
- **Existing per-user identities (follow-up, same day):** MinIO-era access
  keys do not exist on SeaweedFS. `ObjectStoreBackend.user_s3cfg_is_current`
  lets login-time delivery (`api/ops._get_or_create_user_s3cfg`) detect the
  dead key and re-issue the identity (list policy) instead of handing out the
  stale s3cfg; bucket grants come back with the next configuration reload
  (`reload_configuration` → `create_s3_sql_users`). Buckets are recreated
  empty — the maintainer confirmed the MinIO contents are not needed.
- **Legacy data removal (follow-up, same day):** opt-in role tasks
  (`-e umrs_front_purge_legacy_minio_data=true`) remove the retired container,
  `umrs_minio_data` and its bind directory, refused unless SeaweedFS answered
  the `/status` probe in the same converge.
- **2026-09-14 — LIVE on umrs-front.** Evidence (converge `mode=apply`, all
  from branch `fix/umrs-front-umrs-sync` → PR #333):
  - run 34838149148 — full bring-up on the new checkout: postgres ready,
    **SeaweedFS ready in 16 s**, umrs-server image rebuilt, RCCS/AI4S/bdrt/
    octopus HTTP bootstrap ready, RAG + MCP up, `/status` probe 200,
    **7 per-user s3cfg files** awaiting re-issue, **`umrs_minio_data` purged
    (9.7 GB)** incl. `/share/umrs/umrs_minio_data`; all four gateway
    NGINX+sidecar pairs recreated and `/health` 200.
  - run 34839622072 — deploy play complete ("UMRS Front-End Deployment
    Complete"), object store `status=running`; backups timer active.
  - run 34840041494 — reached the observability play; blocked by the
    production Grafana-password assert (no `UMRS_FRONT_GRAFANA_ADMIN_PASSWORD`
    repository secret existed). The maintainer set the secret.
  - run 34845152981 — **clean converge, `failed=0`**: observability stack torn
    down (stale `umrs-front-observability` network dropped — it had outlived
    its containers and the compose provider refused its labels) and recreated;
    Prometheus and Grafana ready. Fixes: PR #334.
- **What the cutover taught (all fixed the same day, PR #333):** the role
  masked umrs fetch/pull failures and deployed a stale checkout (now
  asserted, stderr in the message); data0 reaches the umrs fork through its
  `github` SSH alias (per-repository deploy keys —
  `umrs_front_umrs_remote_url`); an Ansible-rendered file untracked on the
  host blocked the fast-forward (path-limited stash); the compose provider
  records `depends_on` as podman dependencies, so infrastructure containers
  must be removed dependents-first (`rm -f --ignore`); the new bind-backed
  volume directory needs the `:z` relabel and the rendered `iam.json` the
  container label + a readable mode (the image runs weed as root inside a
  user namespace that does not map to the deploy user); `head -n 0` under
  `pipefail` and a refused `image prune` are housekeeping, not failures.
- **Incident (2026-09-14, self-inflicted, recovered):** an interim "leaked
  storage entry sweep" used `podman ps -a --external`, which lists regular
  containers as well, and removed the storage of the live stack (postgres,
  SeaweedFS, the four servers, RAG, MCP, all gateway pairs, most observability
  containers) in run 34837485391. Data volumes were untouched. Recovery: the
  sweep was removed, container removal filters on the `Storage` status,
  `umrs_front_force_full_start=true` recreated the stack (run 34838149148),
  the gateway tasks recreated the edge, `umrs_front_observability_rebuild=true`
  the observability stack. Root cause of the exposure: the earlier masked
  failures had left leaked entries that made a sweep look necessary.
- **Naming (2026-09-14, same day):** backend-branded knobs renamed to S3
  names with the old spellings still honoured for one release — `.env`
  `UMRS_S3_URL` (was `UMRS_MINIO_URL`), connect-info `services.s3_url`
  (`minio_url` mirrors it until clients migrate; the on-disk profile key stays
  `minio_url` for now), step containers get `S3_ENDPOINT_URL` (+ legacy
  `MINIO_ENDPOINT`), start.sh `<CLUSTER>_S3_TUNNEL_ENABLED` /
  `<CLUSTER>_S3_REVERSE_SPEC` / `S3_TUNNEL_STRICT` and
  `<cluster>-s3-reverse-tunnel.{pid,log}`, Ansible `umrs_front_s3_port` /
  `umrs_front_s3_url` / `umrs_front_<c>_s3_tunnel_enabled` /
  `umrs_front_s3_tunnel_strict` / `umrs_front_backup_s3_method`, backup log key
  `s3` and directory `<backup_dir>/s3`, DR `DR_S3_*`. The compute-side host
  alias is now the variable `UMRS_S3_TUNNEL_ALIAS` (BentoML compose) whose
  default stays `minio-host` until every login-node checkout carries the
  variable; flipping the default (and `VISION_S3_PRESIGN_ENDPOINT`) to
  `s3-host:19000` is the last step.
- **Converge flags the GitHub runner still needs** (each a pre-existing gap,
  tracked in STATE `OBJ-20260914-001`): `-e umrs_front_sync_superproject=false`
  (host checkout carries un-upstreamed `observability/` edits),
  `-e umrs_front_hpc_code_sync_enabled=false` and
  `-e umrs_front_slurm_delegation_enabled=false` (runner has no key for
  `<site-account>@AI4S`), `--skip-tags r4_web` (no r4-web build on the runner),
  `-e umrs_front_default_mcp_enabled=false` (privileged Default-MCP runtime
  never installed under `/opt/umrs`).

## Amendment A (2026-09-16): fleet-wide retirement — SeaweedFS everywhere, MinIO support-only

**Decision:** the umrs-front-only cutover is extended to **every deployment
surface**. MinIO stays reachable in code (the `backends/minio.py` adapter,
the `S3_BACKEND=minio` profile, dual `/status`-or-`/minio/health/live`
probes) as the support/rescue lane, but **no compose file, k8s manifest,
Ansible role, or inventory deploys a MinIO server anymore**. Trigger: the
owner's live `podman ps` on umrs-front still showed the pre-cutover
`umrs-minio` container (live-state lag — converge had not re-ran), and the
ask was "stop using minio (while keeping support) anywhere".

**Swapped in this wave:**

- Cluster deploy profiles (`umrs/deploy/{ai4s,rccs,rccs-front,bdrt,octopus,hpbd-atom}`):
  the `minio` service becomes `seaweedfs` (same image/flags shape as
  umrs-front; per-profile published ports preserved — ai4s keeps
  `0.0.0.0:9002`, rccs-front keeps `0.0.0.0:9000`, the rest keep loopback
  9000; IAM/metrics get profile-stable loopback ports to avoid same-host
  collisions). Container servers (ai4s, rccs-front) get canonical `S3_*`
  env **plus a one-wave MINIO_* mirror** pointed at the seaweedfs service
  for older step-worker env readers. Volumes are fresh
  (`s3_data[_ai4s]`, `s3_data`, …) — per-cluster object data was scratch.
- Both deploy templates (`frontend-multi-hpc`, `login-node`) identically,
  including their `start.sh` readiness waits (dual probe) and `env.example`s.
- `umrs/docker/docker-compose.yml` (dev stack) and `umrs/docker/k8s/`
  (`minio.yaml` → `seaweedfs.yaml`, `Service`+`StatefulSet` renamed,
  console port dropped, IAM/metrics ports added, `umrs-s3-secret` with
  `S3_ROOT_*` keys; kustomization + atom-k8s `k8s_infra_components` /
  control-plane wait loop updated to match).
- Ansible `login_node` role: the unversioned native MinIO binary install is
  gone — `tasks/seaweedfs.yaml` installs the pinned `weed` binary (4.47)
  with an `s3` system user, `seaweedfs.env.j2` + `seaweedfs.service.j2`
  (same hardening flags), native spot reserved for the STS/IAM leg
  (deferred — no consumer on native login nodes today). Variable
  namespaces moved to canonical `s3_*` with `minio_*` rows kept as
  **legacy aliases** so pre-existing playbooks (teardown, remote-tunnel)
  keep resolving. `mc` stays installed as a backend-neutral S3 CLI
  (bucket bootstrap + operator tooling — it is a client tool, not a MinIO
  server; same exception as `umrs_front_backup_mc_image`).
- `umrs/deploy/_common/render_seaweedfs_iam.sh` (new): shared IAM/STS
  renderer lifted verbatim from umrs-front's live-verified inline copy
  (umrs-front keeps its copy this wave to avoid churning the data0-
  validated script; unification is a cleanup line below). Cluster shims +
  standalone `start.sh` scripts source it before compose up. Host `.env`
  files from the MinIO era (`MINIO_ROOT_USER/PASSWORD`) keep working: the
  renderer and every compose env block accept them as fallback
  (`S3_ROOT_*` wins when both are set).
- Legacy object-store containers are retired at every `start.sh` run
  (`podman rm -f umrs-minio*`, volumes left for the operator sweep), and
  the readiness probes go dual (`/status` first, `/minio/health/live`
  support fallback) in `_common/start.sh`, the two health-check scripts,
  and both template start scripts. s3cfg placeholders renamed to
  `SET_S3_ROOT_USER/PASSWORD` (the renderer patches live copies on every
  start, so already-rendered host s3cfg files are unaffected).

**Deliberately unchanged (support lane and cousins):**

- CI `ci-minio` service in `.github/workflows/ci.yaml` — it is the
  **adapter support lane** (the minio backend adapter's only exercised
  coverage); seaweedfs adapter conformance already has its own suite.
- `backends/minio.py`, `S3_BACKEND=minio`, `mc` client installs,
  `umrs_front_backup_mc_image`, `MINIO_ENDPOINT` mirrors in step-worker
  env, `VISION_S3_PRESIGN_ENDPOINT=http://minio-host:19000` default
  (host-alias name — flips with every login node carrying `UMRS_S3_TUNNEL_ALIAS`).
- umrs-front's inline `_render_seaweedfs_iam_config` (unify with the
  shared `_common` helper in a later cleanup; the inline copy is the one
  validated on data0 and must not be churned mid-cutover).

**Live cutover order (owner legs, unchanged mechanics from the umrs-front
cutover):**

1. **umrs-front (data0):** reconcile the host checkout (`observability/`
   drift already upstreamed), converge `mode=apply`, verify
   `curl :9000/status`, then the residual `umrs-minio` container + volume
   are removed by the documented purge path (`-e umrs_front_purge_legacy_minio_data=true`).
2. **Clusters (ai4s / rccs / rccs-front / bdrt / octopus / hpbd-atom):**
  next per-site `start.sh` or Ansible converge rides the swap. The old
  `minio_data*` volumes are left in place for a manual sweep after the
  first successful seaweedfs boot, mirroring the umrs-front rule.
3. **k8s (atom-k8s):** next control-plane converge applies the renamed
   manifest; the old `minio` Service/StatefulSet/PVC is pruned by the
   operator (`kubectl delete`) — kustomize does not track renames as
   deletes on applied objects.
