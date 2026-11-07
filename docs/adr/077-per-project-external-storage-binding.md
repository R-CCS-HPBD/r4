# ADR-077: Per-project external storage binding — a project's S3 and Postgres by locator

**Status:** Accepted (agent decision under maintainer delegation, 2026-10-01)
**Date:** 2026-10-01
**Relates to:** gap row D-17 (S3 addressed by project), D-16/D-37 (clients never hold storage credentials), ADR-055 (SeaweedFS backend), ADR-063 (Postgres metadata home), ADR-074 (staged step I/O), ADR-051 (projects registry)

## Context

The maintainer asked on 2026-10-01 for UMRS to use stores **outside the
front-end** for a specific project, "just by specifying the URI/URLs":
HPC sites have large storage next to compute (rccs: Lustre `/lvs0`), while
umrs-front keeps every project's objects on `/share/umrs`. The deployment
itself stays as it is; the test target was a SeaweedFS + Postgres pair
started by hand on the rccs login node, bound to the project `example-project-b`.

What the tree did before this ADR (verified):

- The object store is chosen per **process**, never per project: the HTTP
  API's `S3Config` singleton reads env once
  (`umrs/src/umrs/shared/storage/s3_config.py:19-32, 59-67`); the workflow
  runner builds its client from a hard-coded s3cfg search
  (`umrs/src/umrs/server/workflow_runner.py` `_find_s3cfg`, incl. a literal
  `s3cfg_mnagaso` path); jobs build env clients
  (`umrs/src/umrs/jobs/knowledge_adapters.py` `_build_s3_client`).
- The umrs-front compose hard-codes `DB_HOST: "127.0.0.1"` and
  `S3_ENDPOINT_URL: "http://127.0.0.1:${S3_PORT}"` for every server.
- The projects registry (migrations 0095/0104/0107/0111) already carries
  per-project policy (data class, ask policy, budgets) with owner-or-admin
  verbs — the natural home for a storage choice.
- Most data paths already carry a `project_id`: the files API (verified
  project), the workflow trigger (`params.project_id` → `jobs.project_id`)
  and durable jobs.

The forces: the constitution forbids any new path that hands storage
credentials to a client (§2) and R4 keeping a storage credential (§1.3); a
user-supplied locator makes UMRS open connections to a user-chosen host
(SSRF); and a project's data must never be written to the wrong store
because a lookup failed.

## Decision

**D-1 — The binding object.** A project may carry one storage binding: an
S3 URL `http(s)://ACCESS:SECRET@host:port[/bucket][?region=&verify=&backend=]`
and/or a libpq URI `postgresql://user:password@host:port/db[?sslmode=]`.
It lives in `umrs_db` (`project_storage_bindings`, migration 0114), one row
per project, both locators **sealed** (AES-256-GCM, AAD = project + field;
key = `UMRS_PROJECT_STORAGE_KEY`, else HKDF over `JWT_SECRET`). A project
without a row uses the server's own stores — nothing changes for it.
Libpq keys that name server files or re-target the connection
(`sslrootcert`, `passfile`, `service`, `host`, …) are refused.

**D-2 — Credentials flow one way.** The client sends the locators once
(`r4 project storage set`, from flags, files or an env file; never
persisted by r4). UMRS never returns them: every route answers the redacted
form (endpoint, bucket, access-key hint; host, port, db, user). The §2 row
"no new path hands storage credentials to a client" holds unchanged.

**D-3 — Who may bind, and where to.** Owner-or-admin. Without an operator
allowlist (`UMRS_PROJECT_STORAGE_ALLOWED`, CIDRs or host names) only an
admin may bind; with one, owners may bind targets inside it (and everyone,
admins included, is held to it). Targets resolving to loopback, link-local,
unspecified, multicast or reserved addresses (IPv4-mapped too) are always
refused. A `set` probes the resulting binding from the server first and
refuses on failure (`check: false` skips it).

**D-4 — One resolver, fail closed.** Data paths ask
`umrs.shared.storage.project_storage` for the project's store. It loads the
**set** of bound project ids once per TTL (default 30 s), so an unbound
project costs no per-project read; a bound project's binding is cached per
TTL; a failed refresh serves the last good answer; a project the process
never resolved fails closed (`ProjectStorageUnavailable` → 503 / failed
run / skipped persistence). A missing 0114 table or no Postgres at all means
"no bindings".

**D-5 — Leg 1 consumers.** The object store follows the binding on:
the files API (upload, download stream, presign — upload presigns are pinned
to the bound endpoint, not the server-wide `S3_PRESIGN_ENDPOINT` — batch
presign, metadata, delete, exists); workflow runs with a `bucket` param
(`ctx.s3`, hence the Slurm endpoint and the ADR-074 staged I/O, which the
server performs with `ctx.s3`); durable `workflow_run` artifact persistence.
The Postgres binding is validated and probed (connect, TLS, pgvector
round-trip in a rolled-back TEMP table) but **no data path writes it yet**.

**D-6 — Reachability is the operator's.** A locator must be reachable from
the UMRS server host; the probe proves it. Compute nodes get no new route to
a bound store: on HPC lanes the server stages data in and out (ADR-074 A2).

### Why

- The registry row is where every other per-project policy already lives;
  owner-or-admin verbs and tombstone semantics come for free.
- Sealing at rest keeps the external stores' credentials out of every
  `pg_dump` backup of `umrs_db`; deriving the key from `JWT_SECRET` needs no
  deployment change (the converge already persists it), and a dedicated key
  can replace it without code change.
- Admin-only by default bounds the SSRF surface: any verified user creates
  projects casually (ADR-051), so a casual owner must not be able to make the
  server dial arbitrary internal hosts until an operator says where.
- Fail-closed only for never-resolved projects keeps availability for the
  common case (no binding) when the registry hiccups — the rccs server's
  shared pool was exhausted during this very wave (TODO-20261001-REG class).

## Consequences

- Positive: a project's objects can live on the site's large storage with
  no deployment change; one helper (`umrs/deploy/extdb/umrs-extdb.sh`) starts
  such a pair on a login node; the probe gives an end-to-end receipt from
  the server.
- Cost: every files-API request reads the bound-id set once per TTL per
  process; a binding change reaches other processes after ≤ TTL; rotating
  `JWT_SECRET` without `UMRS_PROJECT_STORAGE_KEY` makes stored bindings
  unreadable (named `sealing_key_changed`; re-set them).
- The HTTP-verified project is still the token's claim (`JWT_DEFAULT_PROJECT`
  = `default` for OIDC users on umrs-front): the files API reaches a bound
  project only for a token scoped to it; workflow triggers declare their
  project (admin, or the token's own project in strict mode).
- NOT in scope (named follow-ups, leg 2): the knowledge plane on the
  project's stores (ingest materials in the bound S3; graph and node
  vectors in the bound Postgres — both together, since ingest writes S3 and
  understand reads it); the staged-lane bucket-ownership check for bound
  stores (`step_staging.py` requires a `BucketsCfg` registration); moving
  data between stores on re-bind; CA bundles for https endpoints beyond
  `verify=false`.

## Alternatives Considered

- **Keep the stores on umrs-front and relay to remote ones** (a local
  forwarder on 127.0.0.1:9000/5432) — rejected: it proves nothing about UMRS
  using an external locator and moves every project at once.
- **A binding per experience in the user JSON config** — rejected: the
  legacy experience model is per user, the credentials would sit in the
  client's config (D-16), and experiences are not projects.
- **Sealing with the ADR-046 recipient keypair** — rejected: the API holds
  only the public half by design; the data paths need the plaintext.
- **Plaintext in `umrs_db` with redaction on read** — rejected: backups.
- **Owner may bind anything non-loopback** — rejected: casual projects make
  that an internal port scanner (probe errors) for every verified user.

## Rollout-status (2026-10-01)

- Landed repo-side: D-1…D-5 (`umrs/src/umrs/shared/storage/project_storage.py`,
  migration 0114, routes `GET|PUT|DELETE /umrs/projects/{id}/storage` +
  `POST …/storage/check`, files/runner/artifact consumers; `r4 project storage
  show|set|check|clear`). Tests: `umrs/tests/test_project_storage_binding.py`
  (63), `umrs/tests/test_project_storage_live.py` (5, env-gated; passed against
  pgvector:pg15 + SeaweedFS 4.47), `r4/tests/test_project_storage_cli.py` (11).
- Live stores: `umrs-extdb-postgres` `<login1>:<pg-port>` (TLS, SCRAM, access
  restricted to the front) and `umrs-extdb-seaweedfs`
  `http://<login1>:<s3-port>` on rccs login1, data under
  `<project-workdir>/umrs-extdb`. data0 → login1 reachability
  verified (S3 `/status` 200, TCP open; Postgres sees the true source address).
- Open: deploy (converge) of the umrs pin; the project role/database and the
  binding registration are credential steps for the maintainer
  (`umrs-extdb.sh project example-project-b`; `r4 project storage set example-project-b --env-file -`);
  live receipt = `r4 project storage check example-project-b`.

## Rollout-status (2026-10-01, later)

- Deployed: the ADR-077 umrs code and migration 0114 went live on rccs with
  the peer apply 36812241003 (umrs `b4d3a12e` contains `1769b23e`); apply
  36817636114 (rccs, parent `cefc8479`, umrs `485e1bbc` = R-14 hygiene kwargs
  on the probe connect) ok=275 changed=35 failed=0 rescued=0, "No pending
  migrations".
- Live receipt: `r4 project storage show example-project-b` answers through the edge
  (typed 404 before the project row existed; after `POST /gw/projects`
  created `example-project-b`, owner `mnagaso`: "no external storage — the server's own
  stores are used").
- Open (owner): the two credential steps (project role + database + URIs on
  login1; `r4 project storage set example-project-b --env-file -`), then the receipt
  `r4 project storage check example-project-b`. The rccs shared `umrs_db` pool still
  exhausts ~20 min after a restart (TODO-20261001-REG class): run the binding
  inside that window, or the projects routes answer bare 500.

## Rollout-status (2026-10-01, binding live)

- Owner credential steps done (16:42 JST): `umrs-extdb.sh project example-project-b`
  (role + database `example-project-b` with pgvector), then
  `r4 project storage set example-project-b --env-file -` — saved after the server-side
  probe: S3 created `r4-data` + `r4-data-output` on the login1 store and
  round-tripped (293 ms); Postgres 15.19 over TLS, pgvector 0.8.6 round-trip
  (52 ms).
- Receipt: `r4 project storage check example-project-b` → probe ok (S3 31 ms, Postgres
  42 ms); the `r4-data` collection's SeaweedFS volume files sit on Lustre
  under `<project-workdir>/umrs-extdb/seaweedfs/data`, owned by
  the operator (keep-id). D-5 consumers for `example-project-b` now read and write the
  login1 store; the Postgres binding is probed, not yet written (leg 2).

## Related

- [ADR-055](055-object-store-backend-seaweedfs.md) — SeaweedFS quirks the external store shares
- [ADR-074](074-uploaded-step-code-runs-sandboxed.md) — staged I/O: the server, not the step, touches the store
- [ADR-063](063-de-facto-answers-recorded.md) — Postgres as the metadata home (rows stay in `umrs_db`)
- `umrs/deploy/extdb/README.md` — the login-node helper and its quirks

*(2026-11-06: internal addresses, paths and group ids replaced with placeholders for the public export (repo-rename plan P5b-2); the values live in r4-ops.)*

*(2026-11-06: partner-demo specifics replaced with neutral wording; the originals are kept in the private r4-private-demo repository (repo-rename plan P5b-3).)*
