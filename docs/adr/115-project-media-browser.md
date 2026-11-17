# ADR-115: Project media browser: Nextcloud and local-directory intake, S3 review streaming and batch triggers

**Status:** Accepted (2026-11-13). Maintainer, on the open decisions O-1…O-11: "please proceed with your decisions". Every O-n is decided as its recommended default (see *Open decisions*, now *Decided*). D-9 (Nextcloud) stays gated as written: nothing of it ships before S7's own legs.
- **Proposed 2026-11-12.**
- **Maintainer request (2026-11-12):** "It might be better if we can stream videos from the S3 bucket connected to umrs-server on the review html. Also, it is great if we can show file trees on Nextcloud (pre-processed) and on S3@umrs-front (post-processed); the former can be used to trigger workflows (multi-video batch jobs enabled) and the latter for review."
- **Maintainer addendum (same day):** "In addition to Nextcloud, the local video files in a specified directory need to be indicated in the input file trees."
- **Agent's part.** Drafted by an agent from three candidate designs and three judge reviews. Choices the maintainer did not state are marked *(agent decision)*; the ones that need a maintainer answer are listed at the end with a recommended default.

**Date:** 2026-11-12
**Relates to:**
- [ADR-065](065-entry-point-console-surface-ladder.md): web console demoted ("no new features"). D-10 here is an amendment.
- [ADR-079](079-researcher-self-service-data-path.md): personal buckets, server-side files list/upload/download. D-3 here amends it for bound stores.
- [ADR-080](080-one-access-guard-shareable-resources.md): bucket access lists (`can_access`). Unchanged.
- [ADR-077](077-per-project-external-storage-binding.md): per-project bound stores.
- [ADR-074](074-uploaded-step-code-runs-sandboxed.md): D-5 owner-only staging, D-8 server-created runs.
- [ADR-097](097-live-project-membership.md): live membership at the gateway.
- [ADR-105](105-umrs-agent-capability-layer.md) D-4 plan→commit; [ADR-108](108-level-hierarchy-worktrees-umrs-scopes.md) scope grants; [ADR-112](112-user-hpc-accounts-gated-delegation.md) signed intents and D-10 custody wording; [ADR-113](113-workflow-versions.md) versions and `source_sha256`.
- [ADR-015](015-knowledge-ingest-egress.md) / CONSTITUTION §2 egress row: D-9 here amends it.
- [ADR-063](063-de-facto-answers-recorded.md): Postgres rows for S3 objects carry URI plus checksum.
- [ADR-051](051-self-service-projects-agent-resource-poles.md) D-3: quota charged per upload.
- Review tool: the generic review player and production review notes in the private demo/review repository.
- Gap rows D-71 … D-80 (proposed 2026-11-12, added to the gap analysis 2026-11-13; see the delivery plan).

## Context

Verified 2026-11-12 on parent `ad07c93b` (umrs pointer as checked out).

**Streaming.** `GET /umrs/files/download/{path}` streams through UMRS, never presigned (`umrs/src/umrs/api/files.py:973-999` `_stream_object`, route `:1011-1034`). It does `head_object` then a full `get_object`, always answers 200 with `Content-Disposition: attachment` (`:996`), and sends no `Accept-Ranges`, `ETag` or `Content-Range`. nginx `/gw/files/download/` forwards the client's `Range` header by default but UMRS ignores it (`gateway/nginx/conf.d/server.conf:2605-2632`). A `<video>` can therefore play progressively but cannot seek past the buffer, and a moov-at-end MP4 cannot start until fully read. UMRS already does ranged S3 reads internally (`umrs/src/umrs/data_catalog/store.py:100-106`). Two dead paths exist: `/gw/stream/` proxies to an `X-Upstream-URL` nothing sets (`server.conf:2737-2758`), and the sidecar `/validate-download` presign + X-Accel route is unreferenced (`gateway/sidecar/main.py:1100-1137`).

**Listing and provenance.** `GET /umrs/files/list` is flat: owned or shared buckets only, `MaxKeys ≤ 1000`, no `Delimiter`, returns key/size/last_modified (`files.py:1042-1076`), and always uses the server-default client (`:1058`); owned-bucket downloads do the same (`:1025`). The workflow runner, however, reads and writes a bound project's bucket pair in the bound store (`umrs/src/umrs/server/workflow_runner.py:1511-1527`), so a bound project's run outputs are invisible to the files routes today. The upload path hashes MD5 only, for quota dedup (`files.py:847-860`); step-lane outputs are uploaded with no ContentType and no hash (`umrs/src/umrs/server/step_staging.py:955-978`). No route returns a SHA-256 for any object, which falls short of ADR-063. The metadata route re-namespaces raw keys into the project (`files.py:301-335, 1343-1380`), so `runs/<id>/...` in an owned `-output` bucket is not addressable. `/executions/{id}/results` lists `artifacts` only for in-memory blobs (`api/workflows.py:2904-2965`; `server/workflow_runner.py:2318-2322`).

**Intake.** The only client-reachable upload is single-request `POST /gw/files/upload` (unlimited body, 7200 s, `server.conf:2527-2559`); `r4 files upload --recursive` walks with `rglob`, one POST per file, no hash, no resume (`r4/src/r4/_cli/files.py:158-170, 208-221`). A chunked lane `/umrs/uploads/manifests` exists but is not routed at the edge, hands out presigned part PUTs (`umrs/src/umrs/api/intake_uploads.py:172-205`, which conflicts with CONSTITUTION.md:112) and assembles parts in a server temp file (`:249-273`). No client surface lists a researcher-chosen local directory; `packages/r4-web` has no file or directory input at all.

**Batch launch.** `POST /gw/workflows/{name}/trigger` exists with the ADR-105 plan gate, ADR-108 scope grants and ADR-112 signed intents (`api/workflows.py:2219-2252`). Its `input_files` only lands in step config (`:160-168`). Staging already stages list-valued params declared in `stage_inputs.params` (`step_staging.py:339-369`, `_param_refs` `:359`) from buckets the run owner owns (`bucket_owned_by`, `:230`). The only multi-video batch today is the ingest trigger's quiet window: in-process, lost on restart, capped at 200, keyed by stem (`server/workflow_triggers.py:128-189`). The partner-specific workflows select by filename stem, which collides across cameras (`sample_215_A/20260330_000001.mp4` vs `_B`).

**Nextcloud.** UMRS has no Nextcloud or WebDAV code. The working path is a client-side script holding an app password in the researcher's env and re-uploading through the files API (`docs/development/wave-ledger.md:2544-2549`). The connector framework is text-shaped (`umrs/src/umrs/connectors/__init__.py:73-90`). The sealed source store is LLM-only and unsealable only by `umrs-infer` (`umrs/src/umrs/security/sealed_sources.py:8-9`; `api/user_sources.py:36-48`). The ADR-077 seal key falls back to an HKDF of `JWT_SECRET` (`umrs/src/umrs/shared/storage/project_storage.py:360-367`), a secret the gateway sidecar also holds. Server egress exists only for knowledge ingest, capped at 10 MB / 120 s (`umrs/src/umrs/knowledge/egress_fetch.py`), and every front denies `134.160.0.0/16` (`umrs/deploy/rccs-front/site.yaml:245-256`), which contains the RIKEN Nextcloud host (134.160.88.3). Probe 2026-11-12: `https://dmsnc0.riken.jp/nextcloud/status.php` answers 200 from the data0 host and from inside `umrs-server-rccs` (Nextcloud 34.0.4 Enterprise, under `/nextcloud/`), so the network path exists and only the UMRS egress policy blocks it.

**Web surface and auth.** `packages/r4-web` has no files, review or workflow view (`packages/r4-web/src/App.tsx:47-62`); ADR-065 demotes it. The `/app/` CSP is `default-src 'self'; … img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'` with no `media-src` (`deploy/ansible/roles/r4_web/templates/app.conf.j2:22`), so same-origin `/gw` video plays and `blob:` video does not. A `<video>` cannot send `X-Project-ID`; it authenticates with the httponly `r4_jwt` cookie and resolves the token's project (`gateway/sidecar/main.py:548-556, 909-932`). The sidecar's membership mode defaults to `token` (`main.py:67`); only the rccs template sets `live` (`deploy/ansible/roles/login_node/templates/gateway-sidecar.rccs.env.j2:26`). ADR-080 `project:<id>` shares already admit only live members (`umrs/src/umrs/shared/storage/user_buckets.py:266-291`).

**What a contributor might do by accident:** hand the browser a presigned URL to make seeking work; read Nextcloud from the browser; add a review DB or a job queue in r4-web; launch runs from a second internal path that skips the trigger route's gates; relax the CSP to `blob:` to make a page work.

## Decision

**D-1. One media read route, ranged.** The existing `GET|HEAD /umrs/files/download/{path}` gains single-range support. Authorization runs first and unchanged (project namespacing, `_require_owned_bucket` / ADR-080 `can_access`), and a ranged request never reaches S3 before it passes.
- `Range: bytes=a-b | a- | -n` → 206 with `Content-Range`, `Content-Length`, `Accept-Ranges: bytes`, `ETag`, `Last-Modified`; `get_object(Range=…)`.
- Unsatisfiable → 416 with `Content-Range: bytes */N`. Multi-range or malformed → full 200.
- `If-Range` with a stale ETag → full 200, so ranges of two object versions are never spliced.
- No `Range` → 200 with `Accept-Ranges` and `ETag`.
- `?disposition=inline` is honoured only for an allowlist (`video/mp4`, `video/webm`, `video/quicktime`, `image/jpeg`, `image/png`, `application/json`, `text/tab-separated-values`, `text/csv`); everything else stays `attachment`. Media responses carry `X-Content-Type-Options: nosniff` and `Cache-Control: private, no-store`.
- No presigned redirect, no `/gw/stream/`. The dead `/gw/stream/` location and the sidecar `/validate-download` route are removed.

**D-2. Object provenance in Postgres.** A table `object_provenance` (PK `bucket, key, etag`) records `size, sha256, sha256_state (pending|verified|mismatch), content_type, source_kind (local_upload|nextcloud|step_output|review_record|legacy), source_ref jsonb, uploader, project_id, batch_id, execution_id, step, workflow_version, workflow_source_sha256, derivative_of (source sha256), created_at, verified_at`.
- Writers are UMRS code paths only: the files upload route (`_HashingReader` adds SHA-256 next to the quota MD5), the intake lane (D-5), step-lane output collection (hash while uploading, set ContentType from the extension), Nextcloud import (D-9) and `OBJECT_VERIFY` (D-5).
- `source_ref` holds a relative path plus a user-chosen root label for local uploads, never an absolute local path *(agent decision)*.
- An object with no row (uploaded before this ADR) is `legacy`; an `OBJECT_VERIFY` job can attest it on demand by re-reading it inside UMRS.

**D-3. One bucket→store resolver.** A single helper resolves the S3 client for (bucket, caller's project) and is used by the runner, download, list, metadata and intake routes alike, mirroring `workflow_runner._select_s3_client` (`workflow_runner.py:1511-1527`): a project with an ADR-077 binding uses the bound store; an unreadable binding answers 503, never a fallback. This amends ADR-079 (personal buckets on bound stores were out of scope): in a bound project, `POST /umrs/buckets` creates the pair on the bound store *(agent decision; open decision O-3)*.

**D-4. Tree listing, metadata and run outputs.** Additive extensions:
- `GET /umrs/files/list` gains `delimiter=/`, returning `prefixes[]` beside `objects[]`; each object gains `etag` and, with `fields=provenance`, the provenance columns (one batched SELECT per page, never a `head_object` per key).
- `GET /umrs/files/metadata/{key}?bucket=` reads the raw key in an owned or shared bucket (no re-namespacing) and returns ContentType, ETag, size, user metadata and the provenance row.
- New `GET /umrs/workflows/executions/{id}/outputs` joins `results.steps[*].output_keys` with provenance and returns, per source video, the source ref and sha256, playback candidates (original, verified derivative), and the events/detections/evidence keys. The client never guesses a `runs/<id>/` prefix. Visibility follows the existing execution rules.
- No extra membership check is added: ADR-080 `can_access` already admits `project:` shares only for live members (`user_buckets.py:266-291`).

**D-5. Resumable intake through UMRS, no presign.** The existing `/umrs/uploads/manifests` lane is reworked rather than a second lane added:
- `create` takes `bucket` (owned; today it is always `config.bucket`), raw `key`, `size`, `sha256_declared`, `client_relpath`, `root_label`, `client_mtime`, `batch_id?`. If provenance already holds a verified `(bucket, key, sha256)`, it returns `already_present` and no bytes move.
- `create` starts an S3 multipart upload through the D-3 client. `PUT …/parts/{n}` streams the raw body (≤ 64 MiB, header `X-Part-SHA256`) through UMRS into `upload_part`, checks the part hash, records part ETag and hash. `GET …/{id}` returns received parts for resume. `GET /manifests?bucket=&prefix=&status=` lists the caller's own manifests.
- `complete` calls `complete_multipart_upload` (no temp-file assembly), charges the disk ledger, and enqueues `OBJECT_VERIFY`, which re-reads the object and computes SHA-256. Match → `verified`; then, and only then, post-upload ingestion runs. Mismatch → the object is deleted, the ledger charge refunded, the row kept as `mismatch`, and the item fails *(agent decision: deletion, not a quarantine prefix the owner could still read)*.
- The presign-part route is removed. The single-request `/files/upload` stays for small files and gains SHA-256 in its reader.
- Abandoned manifests are aborted by the janitor after a TTL.

**D-6. Durable input batches.** Tables `input_batches` (id, owner, project, target bucket and prefix, state) and `input_batch_items` (source_kind `local|nextcloud|bucket`, source_ref, target_key, size, declared sha256, state `needs_upload|transferring|verifying|present|failed`, error). Target keys keep relative paths: `intake/local/<root_label>/<rel_path>`, `intake/nextcloud/<source>/<path>`. A batch is an **intake record, not a launcher**: it never starts a run by itself. Uploads and imports carrying a `batch_id` are catalogued but do not dispatch `on: ingest` workflow triggers, so an explicit batch never double-fires a `batch_quiet_seconds` workflow. Uploads without a `batch_id` behave as today. `POST /umrs/uploads/lookup` answers "which of these (rel_path, size, sha256) are already present and verified", which drives the local tree's status badges.

**D-7. Selection contract on the existing trigger route.** `workflow.json` may declare `selection: {param, form: "refs"|"stems", accept: [".mp4", …], max_items, max_total_bytes, mode: "one_run"|"chunked", chunk_items?}`. Registration refuses a selection whose `param` is not in the root step's `stage_inputs.params` (ADR-113: a new contract is a new version).
- A run is launched only through `POST /{name}/trigger`, or `/plan` then `/commit`, so the ADR-105 plan gate, ADR-108 scope grants and ADR-112 intents all still apply. There is no second launch path.
- `parameters[param]` is a list of `s3://bucket/key` refs or `{"input_batch": "<id>"}`, which the server expands to the batch's present items.
- Validation per ref: bucket owned or shared and owned by the run owner for staging (ADR-074 D-5); `sha256_state = verified` (a `legacy` ref is verified on demand first); extension accepted; count ≤ `max_items`; total bytes ≤ `max_total_bytes` and ≤ the 32 GiB per-step stage cap (`step_staging.py:101-116`), else a typed refusal that names chunked mode.
- `form: "stems"` (older workflows) is refused with `selection.stem_collision` when two refs share a basename stem.
- The server freezes an input manifest `[{bucket, key, etag, sha256, bytes}]`, records it and its digest on the execution beside `workflow_version` and `source_sha256`, and the plan and an ADR-112 intent sign over that digest.
- `mode: chunked` (later slice) splits the frozen manifest into K runs created by the server under one `fan_out_jobs` parent (ADR-074 D-8: the server, not a client, creates them) after the gates pass once.

**D-8. Playback derivatives are workflow outputs.** Browsers that report `canPlayType('video/mp4; codecs="hvc1"')` stream the HEVC original directly (D-1 removes the moov-at-end stall). Otherwise the player uses an H.264 yuv420p `+faststart` derivative produced by a user workflow step on the HPC lane (ADR-074, ADR-110 ffmpeg image), with timestamps passed through (no fps change, VFR preserved), written with a manifest (original and derivative bucket/key/sha256/bytes, codecs, dimensions, duration, frame count, PTS policy, transcoder version and args, validation result). The server, not the step, attests the derivative hash (D-2). `GET /umrs/media/playable?bucket=&key=` returns the best verified derivative for a source sha256 from any of the owner's runs, or a typed `not_playable` reason. The original is never modified.

**D-9. Nextcloud is a UMRS-owned, read-only source (gated).** Nothing below ships before the maintainer's data0 reachability probe and acceptance of this sub-decision.
- **No PHP code (maintainer, 2026-11-12: "please don't use php"; clarified the same day: "if the php was the service probe of outside of umrs, it is ok to use").** R4/UMRS contain no PHP code. Calling an external service's own endpoints is allowed even when their URL ends in `.php`, for example the reachability probe `status.php`. The source itself stays a generic WebDAV (RFC 4918) client using `PROPFIND` and `GET` against the collection URL the owner configures (`kind: "webdav"`); Nextcloud is one server it can point at.
- **Binding:** `POST /umrs/sources {kind: "webdav", base_url, root_path, username, app_password, scope: project|user}`. Project scope is owner/admin only. The credential flows one way and is never returned. The host must be in `UMRS_SOURCE_ALLOWED_HOSTS`, pass the ADR-099 web policy and `denied_networks` (the RIKEN host enters by an exact-host or /32 exception, never by widening `134.160.0.0/16`), and pass a server-side `PROPFIND Depth:0` probe.
- **Custody:** the secret is sealed (the `seal.py` X25519 envelope pattern) to a dedicated recipient whose private key only the `umrs-transfer` worker holds. The API process can seal but cannot unseal; neither the `umrs-infer` key nor the `JWT_SECRET`-derived ADR-077 key is reused *(agent decision; O-5)*. AAD binds owner, source id, host and root_path. User-facing text follows ADR-112 D-10: "UMRS holds a read credential that can act as this Nextcloud account on this folder until you revoke it."
- **Tree:** `GET /umrs/sources/{id}/tree?path=` relays to `umrs-transfer`, which runs `PROPFIND Depth:1` confined under `root_path` (traversal refused), parses with `defusedxml`, caps entries and bytes, caches 60 s per (source, path), and returns `{name, path, is_dir, size, etag, last_modified, content_type, imported_object?}`.
- **Import:** `POST /umrs/sources/{id}/import {items:[{path, etag, size}], dest_bucket, batch_id}` creates a durable `SOURCE_IMPORT` job with one server-created child per file. Each child streams `GET` into an S3 multipart upload on an owned bucket through a SHA-256 reader, checks size and ETag against the selection, writes provenance (`nextcloud`), is idempotent on `(source, path, etag, size)`, caps 8 GiB per file and 2 concurrent transfers per owner, and charges the disk ledger. A code-level verb allowlist permits only `PROPFIND` and `GET`.
- **Egress:** `SOURCE_IMPORT` and source listing are a new egress class; this ADR amends the CONSTITUTION §2 row "Only `MODEL_DOWNLOAD` and `KNOWLEDGE_INGEST` jobs have egress" to add them, under EgressGuard with pinned global-unicast sockets and per-redirect re-checks.
- **Fallback if data0 cannot reach the host** *(O-6)*: `r4 sources pull` on the researcher's machine lists Nextcloud with the researcher's own app password (kept local, never sent to UMRS or the browser) and feeds the same D-5/D-6 intake. The browser's Nextcloud tab then shows the honest state "available through the CLI relay only".

**D-10. Surface: a narrow media view in r4-web (ADR-065 amendment), CLI first.** ADR-065 is amended to admit one generic r4-web route, `/app/media`, with an **Inputs** pane (tabs: *This computer*, *Nextcloud*, *Project bucket*), a **Run** pane (selection basket, workflow@version that declares `selection`, parameters, plan→commit), an **Outputs** pane (S3@umrs-front `-output` tree, grouped by run via D-4), and the review player at `/app/media/review`. Video review cannot happen in a terminal, which is why the web surface is admitted. Every action has a CLI verb that lands no later than its web counterpart: `r4 files tree`, `r4 intake tree|push|status`, `r4 workflow trigger --select|--batch`, `r4 sources add|list|tree|import|revoke`, `r4 media playable`. The web view keeps no job store, review DB or credential; per-viewer conveniences (a remembered directory handle, review drafts) live in browser storage only.
- **Local tree:** `<input type=file webkitdirectory multiple>` everywhere, `showDirectoryPicker` where available. Files are listed locally and badged (not uploaded / uploading / verified / changed) from D-6 lookup. Nothing leaves the machine until the researcher selects files. Hashing runs in a Web Worker with a bundled incremental SHA-256 (WebCrypto is one-shot).
- **Nextcloud tab:** shows an explicit "not connected" state with no import button until D-9 is live (honesty contract FR-2).
- **Player:** a generic port of the review player with a second source kind `umrs_stream`, `src=/gw/files/download/<key>?bucket=<b>&disposition=inline`. Media URLs use only the `bucket=` form, authorized by bucket ACL, so a cookie-only request never relies on the token's project. Identity = (bucket, key, ETag, size, server sha256, derivative manifest digest); `content_hash_checked` is `server_attested`, `browser_full` (optional full download and hash) or `false`, and anything not attested stays `reviewed_unverified_media`. An ETag change mid-session aborts the review. Export metadata takes the project from the session and records `media_source`.
- **Review records:** saved to `<bucket>-output/reviews/<execution>/<reviewer>/<utc>.jsonl` with `create_only=true`, enforced by a provenance reservation row inserted before the PUT (unique on bucket+key) so concurrent saves cannot both win. No review DB.
- **CSP:** `media-src 'self'` is added explicitly; `connect-src 'self'` and `frame-ancestors 'none'` stay. `blob:` media (local preview before upload) is **not** added; that is a separate, reviewed header change *(O-7)*.
- **Project:** a project switcher sends `X-Project-ID` on every `fetch` (GAP-COMP-4).

**D-11. Server-only prefixes and write-once records.** In `-output` buckets, client writes through the files and intake routes are refused under `runs/` (server-written by step collection) and are create-only under `reviews/` *(agent decision; O-8 covers originals and ground truth)*.

**D-12. Edge, limits and audit.**
- New nginx locations `/gw/files/metadata/`, `/gw/uploads/` (per-part `client_max_body_size 80m`, `proxy_request_buffering off`, `upload_limit`), `/gw/sources/`, `/gw/media/`, all with `auth_request /auth/validate` and the signed identity tuple (CONSTITUTION.md:114). `/gw/files/download/` gets explicit `Range`/`If-Range` forwarding, `HEAD`, and `limit_conn conn_limit 8` (zone at `gateway/nginx/nginx.conf:129`).
- State-changing routes reached with the cookie alone (uploads, imports, triggers, review saves) require a same-origin `Origin` check at the sidecar for unsafe methods.
- `data_access_audit` (append-only) records tree, stream (aggregated per user, key, ETag per 10 min), upload, import, source bind/revoke and batch launch; admin read only.
- Authz canaries assert owner-allow and other-deny for every new route; the current deny-always `/gw/uploads` canary (`tools/authz_canaries.yaml:147-149`) is replaced.
- Rollout precondition: `GATEWAY_PROJECT_MEMBERSHIP=live` on every front that serves `/app/media`.

### Why

- **Streaming through UMRS with Range** is the only design that meets CONSTITUTION.md:112-113 and makes seeking work; it extends a route that already exists and already authorizes correctly.
- **Server-attested SHA-256** turns the review tool's "content_hash_checked: false" into a real check without asking the browser to hash a ranged stream, and meets ADR-063.
- **One resolver** stops the files routes and the runner disagreeing about where a bound project's data lives, which would otherwise make the output tree empty for exactly the projects that use bound stores.
- **Batches that do not launch** keep one launch path, so every existing gate (plan, scope grant, signed intent, version pin) applies to tree-driven runs too.
- **Relative-path keys and refs** fix the cross-camera stem collision without new matching logic.
- **A dedicated unseal consumer** keeps the Nextcloud bearer credential out of the API process and out of anything holding `JWT_SECRET`.
- **CLI first** keeps ADR-065's ladder honest; the web view exists because video review needs a browser.

## Consequences

**Positive.**
- Reviewers seek multi-GB videos in the browser through `/gw` with no credential or presigned URL in the page.
- Researchers see three input trees (local directory, Nextcloud, project bucket), select many files, and launch one run (or chunked runs) whose execution records exactly which bytes it ran on.
- Every object UMRS writes from now on has a SHA-256 and an origin.
- Interrupted multi-GB uploads resume from the last part.
- Two dead presign-shaped paths leave the edge.

**Cost / negative.**
- Part bytes and video ranges both flow through UMRS: the front's bandwidth and worker pool carry them (ADR-079 accepted this cost; sizing is a rollout leg).
- `OBJECT_VERIFY` re-reads each uploaded object once.
- Derivatives roughly double storage per transcoded video.
- A new `umrs-transfer` container, a keypair and a CONSTITUTION row amendment for Nextcloud.
- r4-web gains a test harness and a real feature, against ADR-065's original demotion.
- Complexity ≤ 9 forces splitting `_stream_object`, `list_owned_objects` and the step-staging upload while extending them.

**Not in scope.**
- Local preview of not-yet-uploaded files in the browser (needs CSP `blob:`; O-7).
- A watched local folder that auto-uploads new recordings.
- Writing back to Nextcloud.
- Object lock or versioning for originals and ground truth (O-8).
- Per-video parallel fan-out beyond chunked mode.
- Making the ingest quiet-window batches durable (tracked separately; batches here replace them for UI-driven runs).

## Alternatives Considered

- **Reuse-first as drafted** (the base of this ADR). Kept: extending existing routes, reworking the manifests lane, the `selection` block on the existing trigger route, `fire_triggers` suppression, retiring dead paths, review-first phase order. Changed: its Nextcloud seal used the ADR-077 key (derivable from `JWT_SECRET`); its selection validator was lenient and its summary strict; it verified after firing triggers; it quarantined in the owner's own bucket; it left bound stores open.
- **Governance-first.** Kept: dedicated unseal consumer, verify-before-trigger, input-manifest digest signed by plan and intent, relative path plus root label only, aggregated audit, stem-collision refusal, response scans for `X-Amz-Signature`, live membership as a rollout precondition. Rejected: a second launch route (`batch-runs`) beside batches-with-launch; a bucket-project membership check on every read, which would change ADR-080 share semantics and is redundant with `can_access`; a typed 503 for bound projects instead of fixing parity; deferring the ledger charge until verify; a nine-phase order that delivers the web review last.
- **Researcher-first.** Kept: durable input batches with per-item state, `batch_id` suppression of ingest triggers, relative-path target keys, HEVC passthrough via `canPlayType`, the outputs manifest, a Project bucket tab, HEAD, CSRF posture, `defusedxml`, bound-store parity, the local CLI relay fallback. Rejected: auto-launch through the internal ingest path (skips ADR-108 grants and ADR-112 intents); shipping CSP `media-src blob:` as routine; a `?project=` query mapped to `X-Requested-Project-ID` (a new identity input at the edge; bucket-form URLs make it unnecessary); keeping `only_videos` stems without collision refusal; four separate ADRs.
- **Presigned GET redirect for `<video>`.** Rejected: CONSTITUTION.md:112.
- **Browser talks to Nextcloud.** Rejected: puts the credential in the browser (CONSTITUTION.md:113) and is cross-origin under `connect-src 'self'`.
- **Extend the RAG connector framework for Nextcloud.** Rejected: `Document.content: str`, in-memory schedules, plaintext credentials.
- **Rely on nginx to slice full 200s into 206.** Rejected: UMRS would still read the whole object per request.

## Rollout-status (2026-11-13)

- **S0 (docs) landed 2026-11-13** with S1 on `wave/adr115-s1-streaming` (ledger (283)): this ADR Accepted; amendments recorded in [ADR-065](065-entry-point-console-surface-ladder.md) (`/app/media`), [ADR-079](079-researcher-self-service-data-path.md) (bound stores, O-3) and `CONSTITUTION.md` §2 (egress row, text only, activates with S7); gap rows D-71…D-80 added. Maintainer probe 1 (Nextcloud reachability) passed 2026-11-12; probes 2–4 stay open.
- **S1 repo-side 2026-11-13** (umrs `b672e02d`): D-1 ranged `GET|HEAD /umrs/files/download` (`umrs/src/umrs/api/_ranged_download.py`), D-3 `resolve_bucket_store` used by the runner, the files routes and bucket creation, D-12 edge part (`Range`/`If-Range` forwarded, `limit_conn conn_limit 8`, `/gw/stream/`, `/internal/s3/` and the sidecar `/validate-download` removed, canary `files-download-media`). Not deployed: converge is a maintainer leg. Deferred to later slices as planned: `Origin` check, `data_access_audit` and the other new `/gw` locations (D-12), everything in S2–S9.

2026-11-12 (Proposed): nothing landed. Slices (detail in the delivery plan):

| Slice | Decisions | Content |
|---|---|---|
| S0 (docs, ops probes) | all | This ADR accepted; ADR-065, ADR-079, CONSTITUTION §2 amendment texts; gap rows D-71…D-80; maintainer probes |
| S1 (umrs, gateway) | D-1, D-3, D-12 | Range/206/416, ETag/If-Range, HEAD, inline allowlist, one bucket→store resolver, edge headers and limits, retire `/gw/stream/` and `/validate-download` |
| S2 (umrs, r4) | D-2, D-4, D-11, D-12 | `object_provenance`, SHA-256 on upload and step collect, delimiter list, raw-key metadata, executions outputs, server-only `runs/`, create-only `reviews/`, audit, `r4 files tree` |
| S3 (r4-web, gateway, ops) | D-10, D-12 | `/app/media` Outputs pane + review player, project switcher, test harness, `media-src 'self'`, Origin check, live membership on canary front |
| S4 (umrs, r4, r4-web) | D-6, D-7 | Selection block, trigger/plan/commit validation, input manifest digest, `OBJECT_VERIFY` for legacy refs, Project bucket tab, Run pane, `r4 workflow trigger --select` |
| S5 (umrs, gateway, r4, r4-web) | D-5, D-6 | Proxied resumable intake, batches, lookup, *This computer* tab, `r4 intake` |
| S6 (workflow step, umrs) | D-8 | Transcode step + manifest, `media/playable`, partner-specific workflow version (private repository) |
| S7 (umrs, gateway, r4, r4-web, ops) | D-9 | Nextcloud binding, `umrs-transfer`, tree, `SOURCE_IMPORT`, CONSTITUTION row; or the S7-alt CLI relay |
| S8 (umrs) | D-7 chunked | Server-created chunked runs |
| S9 (ops) | all | Member-only canary, then all fronts |

## Open decisions for the maintainer (decided 2026-11-13)

Each has a recommended default the agent will use unless told otherwise.

**Decided 2026-11-13.** The maintainer answered "please proceed with your decisions", so each O-n below is decided as its *Default*: O-1 r4-web `/app/media`, CLI first; O-2 `one_run`, chunked in S8; O-3 files routes follow the runner (bound store), ADR-079 amended; O-4 `verified` provenance required, on-demand `OBJECT_VERIFY` for `legacy`; O-5 dedicated `umrs-transfer` recipient key, project service-account app password on a dedicated share; O-6 S7-alt CLI relay if S7 is blocked (probe 1 passed, so S7 is the path); O-7 no CSP `blob:`; O-8 `intake/` write-once now, `gt/` deferred; O-9 generic transcode step public, partner-specific wiring private; O-10 live membership per front before `/app/media`; O-11 D-9 stays in this ADR as a gated sub-decision.

- **O-1. Surface.** Amend ADR-065 for `/app/media` in r4-web, or host trees and review in VS Code first? *Default: r4-web `/app/media` (the request says "the review html"); CLI verbs land first; VS Code later through ADR-103 if wanted.*
- **O-2. Batch default mode.** One run over N videos, or chunked runs? *Default: `one_run`; refuse above `max_total_bytes` with a pointer to chunked mode; chunked lands in S8.*
- **O-3. Bound stores.** Make files routes follow the runner's bound-store choice and create personal buckets on the bound store in bound projects (ADR-079 amendment), or refuse with 503? *Default: follow the runner (parity), because run outputs of bound projects already live there.*
- **O-4. Strictness of selection.** Require `verified` provenance for every ref? *Default: yes, with on-demand `OBJECT_VERIFY` for `legacy` objects, so old uploads stay usable after one server-side hash.*
- **O-5. Nextcloud credential custody.** Dedicated `umrs-transfer` recipient key, or the ADR-077 project seal? *Default: dedicated recipient key in its own container; a read-only project service account app password on a dedicated share, bound by the project owner, rather than personal passwords.*
- **O-6. Nextcloud network path.** If the data0 probe fails or a /32 exception for the RIKEN host is not acceptable: *Default: ship S7-alt (local CLI relay into the same intake) and keep the browser Nextcloud tab in the honest "CLI relay only" state.*
- **O-7. Local preview before upload.** Add CSP `media-src blob:` to `/app/`? *Default: no; local files are listed, not played, until uploaded; the offline player keeps local preview.*
- **O-8. Immutability of originals and ground truth.** Make `intake/` originals and `gt/` write-once too? *Default: yes for `intake/` (server-written by D-5/D-9 only), `gt/` deferred to a separate governed decision.*
- **O-9. Public example.** Ship the generic transcode step in the public R-CCS-HPBD/r4 export? *Default: yes, generic only; partner-specific wiring, buckets and execution ids stay in the private demo/review repository.*
- **O-10. Membership mode.** Set `GATEWAY_PROJECT_MEMBERSHIP=live` on ai4s, bdrt and octopus before `/app/media` goes there? *Default: yes, as a rollout precondition per front.*
- **O-11. ADR split.** Keep Nextcloud (D-9) inside this ADR or split it into a separate ADR? *Default: keep it here as a gated sub-decision; split only if its review blocks S1–S6.*

## Related

- [ADR-063](063-de-facto-answers-recorded.md), [ADR-065](065-entry-point-console-surface-ladder.md), [ADR-074](074-uploaded-step-code-runs-sandboxed.md), [ADR-077](077-per-project-external-storage-binding.md), [ADR-079](079-researcher-self-service-data-path.md), [ADR-080](080-one-access-guard-shareable-resources.md), [ADR-097](097-live-project-membership.md), [ADR-105](105-umrs-agent-capability-layer.md), [ADR-112](112-user-hpc-accounts-gated-delegation.md), [ADR-113](113-workflow-versions.md)
- `CONSTITUTION.md` §1.3 ownership table, §2 rows (egress, presigned URLs, storage credentials, auth_request)
- Delivery plan: project-media-browser-plan-2026-11-12.md
