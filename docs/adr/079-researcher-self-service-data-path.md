# ADR-079: Researcher self-service data path — upload, personal buckets, files, results

**Status:** Accepted (agent decision under maintainer delegation, 2026-10-02)
**Date:** 2026-10-02
**Relates to:** ADR-027 (workflow upload role), ADR-074 (uploaded code on HPC lanes; D-5 staged I/O, D-8 owner-scoped runs), ADR-077 (per-project external storage), gap rows D-16 / D-37 (clients never hold storage credentials), D-17 (S3 by project)

## Context

On 2026-10-02 a partner video-analysis pipeline ran on the rccs GB10 lane (ADR-074
receipts R3–R5), and the maintainer asked to share it with colleagues — and
chose "make it self-service first" over an admin-assisted guide. A code map
the same day (verified against r4 HEAD and umrs `a039dc7a`) found that a new
colleague could log in but could not run the pipeline without an admin:

- Workflow upload required the `workflow_author` role
  (`umrs/src/umrs/api/workflows.py` `_enforce_workflow_upload_role`); the
  gateway sets it only from a token `roles` claim, Okta tokens carry none,
  and nothing grants it by default. The maintainer decided on 2026-09-25
  that normal users upload their own workflows (memory/STATE SEC-20260925-001).
- The lane stages only buckets the run owner owns
  (`umrs/src/umrs/server/step_staging.py` `_check_owned` +
  `_check_registered`): `<owner>.<project>.<alias>` AND a `BucketsCfg` entry
  — which only server-side provisioning of an enrolled user's config creates.
- Data reached such a bucket only with per-user S3 credentials, whose
  delivery stopped on 2026-09-22 (D-16); `r4 files upload` writes only the
  shared `r4-data` bucket, which the lane refuses.
- `r4 files download` read only `r4-data` / `r4-data-output`, so results in
  the run owner's `-output` bucket were unreachable; no CLI verb printed a
  run's per-step outputs.

## Decision

**D-1 — Verified users upload their own workflows.** A gateway-verified
identity may upload a `user`-scope workflow without the `workflow_author`
claim (`UMRS_WORKFLOW_UPLOAD_ROLE=verified`, the default); `scope=system`
stays admin-only; a site restores the ADR-027 role gate with
`UMRS_WORKFLOW_UPLOAD_ROLE=workflow_author`. The code still never runs in the
server (ADR-074) and a user-scope workflow runs only for its owner (D-8).

**D-2 — Personal buckets.** `POST /umrs/buckets {project, alias}` (gateway
`/gw/buckets`, verified caller) creates `<caller>.<project>.<alias>` and its
`-output` pair on the server's own object store and records the owner in
`user_buckets` (migration 0115). Each part becomes one segment (lowercase
letters, digits, inner hyphens; spaces, `_`, `@` and `.` become `-`, so an
e-mail-shaped OIDC `sub` works; ≤ 56 characters so `-output` fits); a name
another owner holds (personal or provisioned) is refused — including a
second identity that maps to the same segment. `GET /umrs/buckets`
lists the caller's personal and provisioned buckets.

**D-3 — One ownership rule.** `step_staging.bucket_owned_by(bucket, owner)`
is the rule for both the HPC lane's staging check and the files API: a
personal bucket whose server-written `user_buckets` row names the owner, or
a provisioned bucket whose name carries the owner's prefix AND whose
`BucketsCfg` entry names them; an `-output` name belongs to its input
bucket's owner. A lookup failure never grants ownership.

**D-4 — Files in owned buckets, through UMRS only.** The files API accepts a
`bucket` the caller owns: `POST /umrs/files/upload` (`bucket` form field,
raw key, streamed with `upload_fileobj`, disk ledger charged from the
streamed md5 + size), `GET /umrs/files/download/{key}?bucket=`, and the new
`GET /umrs/files/list?bucket=&prefix=&token=` (gateway `/gw/files/list`).
The server performs every S3 call; no credential and no presigned URL
reaches the client (constitution §2).

**D-5 — r4 verbs.** `r4 bucket create|list`; `r4 files upload --bucket
[--recursive]`, `r4 files list --bucket [--prefix]`, `r4 files download
--bucket [--recursive -o DIR]` (streams to disk); `r4 workflow results <id>`
(per-step `output_keys` and extras, plus the download command).

### Why

- The maintainer's 2026-09-25 decision makes uploads a user capability; the
  role gate protected nothing ADR-074 does not already contain, and it made
  every colleague an admin ticket.
- A table next to `BucketsCfg` adds ownership without enrolling users in
  server configs, and works for identities a config sync refuses (numeric
  OIDC subjects fit the name rule).
- Routing data through the existing files API keeps D-16/D-37: R4 never
  touches a bucket, and the server stays the only S3 client.

## Consequences

- Positive: a colleague installs r4, logs in, creates a bucket, uploads
  data and weights, uploads and triggers the workflow, and downloads the
  results — with no admin step.
- Cost: uploads stream through the server process (one connection per
  file, `client_max_body_size 0` already on `/gw/files/upload`); personal
  buckets have no quota of their own beyond the disk ledger.
- NOT in scope: sharing one workflow across users (grants are not
  consulted by run resolution; each colleague uploads their own copy);
  personal buckets on ADR-077 bound stores; deleting buckets; a CLI flag for
  `scope=system`.

## Alternatives Considered

- **Admin-assisted onboarding** (mint `workflow_author` tokens, enrol each
  user, load their data) — rejected by the maintainer (2026-10-02).
- **Hand out per-user S3 credentials again** — rejected: D-16, constitution §2.
- **Let the lane stage the shared `r4-data` bucket** — rejected: one bucket
  for every user breaks the owner-only staging rule (ADR-074 D-5).
- **Register personal buckets into `BucketsCfg` only** — rejected: the
  registry is rebuilt from user configs at boot, so the rows would vanish.

## Rollout-status (2026-10-02)

- Landed repo-side: D-1…D-5 (umrs `7870bac3` + `0c6aee0b`: migration 0115,
  `shared/storage/user_buckets.py`, `api/buckets.py`, `api/files.py`,
  `server/step_staging.py`, `api/workflows.py`; parent: `r4` CLI,
  `gateway/nginx/conf.d/server.conf`, `r4-contracts` error codes). Tests:
  `umrs/tests/test_personal_buckets.py` (19 + 1 Postgres round trip, passed
  against postgres:15), role-gate default tests, `r4/tests/test_files_cli.py`
  (+7), `r4/tests/test_workflow_results_cli.py`.
- Open: deploy (converge) and the live receipt on rccs.

## Rollout-status (2026-10-02, live on rccs)

- Deployed: apply 36978114763 (parent `99148667`, umrs `94051b34`), ok=275
  failed=0; migration 0115 applied by the converge ("Applied migration: 0115").
- Live receipt — the colleague guide walked step by step as a user
  (a partner project's colleague guide, kept privately):
  - `r4 bucket create example-project-b receipt` made `<user>.example-project-b.receipt` (+ `-output`);
    `r4 bucket list` showed it next to the provisioned buckets.
  - `r4 files list` / `r4 files download` read a provisioned bucket.
  - Streamed uploads into the personal bucket: model weights, plus a nested
    data folder with `--recursive`.
  - `r4 workflow upload` registered the zipped bundle; `r4 workflow trigger … --wait`
    ran it as `wf-<id>` on the GB10 lane: the GPU step ran on cuda.
  - `r4 workflow results` printed the steps; `r4 files download runs/<id>/ --bucket
    …-output --recursive` fetched every output object.
- Found by the receipt: a single-object download to a nested `-o` path failed
  and held the body in memory — fixed in r4 (parent `c9708559`, streams to
  disk).
- Not yet exercised: a colleague's browser (Okta PKCE) login. The client
  allows `authorization_code`, and the rccs `/auth/cli-callback` answers 200.
- Other fronts: the code reaches ai4s/bdrt/octopus with their next converge
  (owner dispatch).

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
