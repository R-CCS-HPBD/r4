# ADR-046: Remote session credential delivery + egress (frontend venue)

**Status:** Proposed (R0+R1 landed repo-side 2026-09-09; live-venue legs verified 2026-09-09/10; Amendment 2 wave landed repo-side 2026-09-21, live legs pending)
- **Implements:** remote-session-credential-egress-plan-2026-09-09
  (design — decisions D-1…D-7 restated + pinned below as normative)
- **Related:** ADR-015 (ingest egress), ADR-041 (assets grants), ADR-043
  (attempt-scoped credentials), ADR-044 (off-books job classes),
  api-key-handling-audit-2026-09-09
  (findings G-1…G-9 superseded/fixed here)

## Context

Frontend-venue companion sessions ("remote" venue) run the companion agent
in a sandbox on the UMRS frontend host. Users want their **own provider
credentials** (public-kind sources: base URL + API key) available remotely
with local-session parity, without the agent possessing the key.

## Decision (normative)

P1. **The worker never holds the provider key** (D-1). Provider calls exit
through the credential-injecting inference proxy in the UMRS trust zone.
The worker holds only a scoped session token (lease: session_id,
source_ids, egress_ip, TTL, max-lifetime cap).

P2. **Recipient-keypair sealing at rest** (D-3). The UMRS API seals with
the public half at push; the proxy unseals per request with the private
half. Ciphertext cannot be re-pointed at another owner/host (AAD).

P3. **Two egress classes, two paths** (D-4). Inference: pinned host,
injected credential, path policy per provider kind, otherwise unlimited
(streaming, big bodies, long timeouts — the full-capability contract).
Web: uncredentialed forward proxy, public internet default, infrastructure
ranges denied, opt-in allowlist.

P4. **Worker and sidecar share no netns** (D-5); worker sits on a
per-session `--internal` network with no default route; the dual-homed
egress pod is the only path out. **Fail-closed isolation self-test** before
the agent loop (exit 78) (D-7).

P5. **Per-request lease revalidation from Postgres** (D-6) — revocation
denies the next request; no caching, no TTL tail.

P6. **Worker is a CPU-only agent sandbox** (Amendment 1): no vLLM, no GPU
devices, no model mounts. Reuse from mini-RiVault limited to the
network/egress machinery.

## Rollout status

| Wave | Scope | State |
|---|---|---|
| R0 (2026-09-09) | `/umrs/secrets/*` verified-identity envelope + owner pinning (G-6) · durable broker leases (0085 `secret_leases`/`secret_audit`, inference bindings, cascades, restart test) · SSRF base_URL validator · `r4` credential hygiene (`--api-key-env/-stdin`, G-4 refusal, remove-cleanup) | **Landed** |
| R1-1 | 0086 `user_sources_sealed` + column grants + `security/seal.py` envelope (X25519 + HKDF + AES-GCM; PyNaCl-equivalent on `cryptography`) + push/unpush/list/audit routes + revoke cascade | **Landed** |
| R1-2 | `umrs.infer_proxy` service (leases, policies for openai/openai_compat/anthropic/azure_openai/google, URL join, streaming, redacting log filter, `/healthz`) | **Landed** |
| R1-3 | `umrs/venues/frontend_podman.py` provisioner (ordered steps + compensating teardown) + nginx/squid templates + entrypoint self-test | **Landed** |
| R1-4 | `session_venue_runtime` (heartbeat renew, idle/expiry sweep + teardown) | **Superseded 2026-09-21** by the `frontend_session` job status checker (P10) — same rules, per job attempt |
| R1-5 | `r4/venue.py` (token-file fallback in providers, spawn-env sanitization) | **Landed** |
| R1-6 | `r4 source push/unpush`, `list --remote` (pushed/stale/tombstoned/-/? badges) | **Landed** |
| R1-7 | `tools/canary_credential_sweep.py` (0-hit rule, allow-globs, live-mode gating) | **Landed** |
| R2 (2026-09-09) | nftables render (playbook apply at deploy), TOCTOU pin via sni_hostname, per-session rate limiter, `r4 source audit` + lease countdown, keypair rotation job — all repo-side landed | **Landed repo-side** |
| R3-1/R3-2 (2026-09-09) | source grants (0087 + routes + revoke cascade) · retention (TTL purge + export + tombstone TTL) | **Landed repo-side** |
| R3-3/R3-4/R3-5 | KMS unseal (site KMS procurement), realtime/WS (own design package), gVisor/Kata runtime class (runtime support decision) | Planned |
| Live legs (2026-09-09, umrs-front) | checkout converged to `umrs@5db8ac12`/parent `5c1de033`; **0085/0086/0087 applied+registered on live `umrs_db`** (registry rows 0085–0087 beside 0083); keypair bootstrap (`UMRS_SEAL_PUBKEY_B64` in env, privkey as podman secret); umrs-server image rebuilt; all 4 cluster servers restarted on it; **`umrs-infer` container live** (`/healthz` ok); **gateway-nginx rebuilt with `/gw/user-sources` + `/gw/secrets` lane conf and all 4 lanes recreated** (route type-confirmed: 302 auth chain on every lane); **canary leg pass**: push 201 (fingerprint public via fingerprints only, metadata never carries ciphertext); lease minted + egress-IP bound; inference legs — inject+unseal+reach (upstream LiteLLM received the canary key and honestly rejected it), path policy deny 403 `path_denied`, `sid_mismatch` 401, `token_invalid`, streaming pass-through; unpush → tombstone+`leases_revoked=1` → **next request `lease_revoked` 401**; broker audit rows show issue/bind/infer/deny/revoke lifecycle with no bodies/tokens/keys; **0-hit canary sweep across all container logs, `.env`, session dirs, env vars; ciphertext column alone has the blob (tombstoned, by design)**; `r4 source push|list --remote|audit|unpush|remove` all work through Apache+Apache-lane+nginx+UMRS publicly with a normal user JWT | **VERIFIED LIVE** |
| Wave 2026-09-21 (ADR-046 A2 — remote sessions from the TUI, repo-side) | umrs: F9 regression fixes (`turns/{turn_id}` decorator, taxonomy codes, auth marker), `companionvenue.*` family, `FrontendVenueConfig` (`UMRS_VENUE_*`), migration 0099 + `VenueSessionStore`, `/…/{sid}/venue/start|stop` + `GET /…/venue` (409 `source_not_pushed`), lease-token rail (`_rail_identity`, `/…/{sid}/rail/*`: snapshot GET/POST, queue/pop, heartbeat, approvals), sweeper lifespan wiring, provisioner rail listener + worker argv/env; r4: `venue_rail.py`, `venue_worker.py` (`r4 venue worker`, D-7 self-test in Python, T4 approvals, heartbeat partials), bridge verbs `session.venue_start/status/stop/sync/approvals/decide` + `source.list_remote/push/unpush`, `send_message` remote branch, `r4 session start/venue/stop`, `--json` on push/unpush, queue dict-row tolerance; TUI: remote source picker (push-on-select, web policy), remote chat watcher (partial deltas → adopted row), status chip, `/venue`, sessions `t stop`, `/source` p/u, remote approvals via the tool card; deploy: `frontend-venue.yaml` (openssl keypair → env/podman secret, egress-net, pinned images, worker image build, `.env` lines), `frontend-venue-infer.yaml` (umrs-infer bridge-native), opt-in privileged nftables lane, compose socket/session-root/venue env. Suites: umrs 5142 (+46 new; one pre-existing env-sensitive red `test_connection_profiles` from the host's `s3_url` profile key), r4 3332, bun 1044, route inventory 362 OK, tsc clean. **Live legs pending**: converge `--check` → apply on umrs-front, then a session-start receipt (push → Server venue → queued turn → assistant row adopted) recorded here. | **Landed repo-side** |
| Converge check (2026-09-21, run 35579509729 on parent `0b47d409` / umrs `403b0db7`) | Host checkout fast-forwarded (superproject + umrs); every `Frontend venue —` task evaluated as designed in check mode: keypair present-probe ok, adoption/generation correctly skipped (check), continuity assert ok, `egress-net` present (gateway `172.20.8.1` → `UMRS_VENUE_INFER_RESOLVE_VIA`), squid image already pulled (digest-pinned in the `.env` diff), nginx `1.27-alpine` + worker image build deferred to apply, `.env` diff = the 15 `UMRS_VENUE_*` lines. The run ended `failed=1` on the **pre-existing** check-mode defect `Wait for auth sidecar /health` (`uri` module "does not support check mode") — identical fatal line on every scheduled check since 2026-09-17 (runs 35277411556 … 35537176340). Follow-ups landed before apply: worker reports bootstrap failures through the rail (`ea22ea5a`), sweeper broker without a jobs→api import (umrs `7bc7a65f`, parent `41ab0275`). Pre-deploy live observation: on the rccs server the shared-pool routes (`/user-sources/sealed`, `/companion/sessions`) answer 500 while `/secrets/lease` answers 200 — the known PG-pool-zombie class the restart heals. | **Check evaluated; apply pending owner action** (`gh workflow run converge-umrs-front.yaml --ref main -f mode=apply -f extra_args="-e umrs_front_hpc_code_sync_enabled=false -e umrs_front_slurm_delegation_enabled=false -e umrs_front_default_mcp_enabled=false --skip-tags r4_web"`), then `python tools/remote_session_receipt.py rikyu glm-5.3-flash` from a logged-in workstation |
| P10 revision — one task table (2026-09-21, repo-side) | Session runtime lifecycle folded into the durable `jobs` table: `JobType.FRONTEND_SESSION` + `umrs/jobs/frontend_session_backend.py` (backend provisions on claim; status checker = lease/idle/exit authority; cancel-bus teardown), lane built in `DurableOrchestrationService.startup()` where `UMRS_VENUE_ENABLED`; `POST …/venue/start` submits the job (venue = `load_site_config().name`, `restart_policy=never`, ADR-051 spawn budget enforced) and answers `provisioning`; `GET …/venue` carries `job` (status + attempt detail); stop = teardown + `cancel_job`; migration 0100 (`companion_venue_sessions.job_id`); rail gw passthrough `ANY …/rail/gw/{workflows|jobs}/…` with gateway-minted identity + `spec.origin` on created jobs; `AGENT_JOB_KINDS` fixed (`companion_worker` was never counted) + `frontend_session`; sweeper (`venue_sweeper`, `session_venue_runtime`, `UMRS_VENUE_SWEEP_*`, compose/group-var flags) retired; r4 `build_rail_platform_client`, `r4 session start` job line + provisioning hint; TUI fast-poll until running/failed; receipt script waits for `running`. Plan rows G-RS-30…36. | **Landed repo-side** — apply + receipt still owner-side (command in the row above) |
| Apply attempt 1 (2026-09-21, run 35608700603 on parent `5fa04659` / umrs `5a2badc7`) | Keypair adoption + continuity assert passed, egress-net present, sidecar images pulled; **failed at the worker image build**: `r4/Dockerfile` referenced a nonexistent `r4/hatch_build.py`, the root `.dockerignore` excluded `r4/`, siblings `r4-teamengine-spi`/`science` were not installed registry-free, and the venue include ran before the host checkout fast-forward. Fixed in the follow-up commit (Dockerfile, `.dockerignore`, include order); image validated locally (267 MB, imports OK, `venue worker` exits 4 without a rail). | **Failed → fixed, re-applied** (see next row) |
| Apply attempts 2–3 (2026-09-21, runs 35609874627 / 35613298668 on parent `e9fbd0d9`→`6593b32e`, umrs `5a2badc7`) | **Servers restarted on the new code** (start.sh --server 17 min; pending migrations `0098`, `0099`, `0100` applied at boot — `GET …/venue` answers a typed 404 live), worker image `localhost/r4-companion:latest` built from the repo root, gateway/auth health green; **failed at the umrs-infer health assert**: run 3's new diagnostic showed the proxy crash-looping (891 restarts) on `connection to server at "169.254.1.2", port 5432 failed: Connection refused` — Postgres is published on `127.0.0.1` only and, under the host's podman 5.8.2, pasta maps `169.254.1.2` to the host's primary address, not loopback. Fix: umrs-infer joins the Postgres container's network and dials `umrs-postgres:5432` by name (stale detection now also covers DB path/network changes; diagnostic stays). | **Failed → fixed, re-applied** (see next row) |
| Apply attempt 4 (2026-09-21, run 35614300721 on parent `8e0ab9f2`) + receipt 1 | **Venue chain converged**: umrs-infer healthy on the Postgres network (0 restarts), every `Frontend venue —` task ok, services reconciled; the play ends red only on an unrelated pre-existing assert. Receipt 1 from the workstation: push ok, session mirrored, `venue/start` **503 idempotency key 66 > 64 chars** → fixed in umrs `1333ee22`; re-apply + receipt 2 follow. | **Converged; receipt pending the key fix** |
| Post-leg ops lines | Host-side hardening and deploy-role follow-ups from the leg are tracked in the operations runbook, not in this ADR. | Noted |
| Two-client approval probe (2026-09-10, `ask-probe-v1` via `job-14d4eed4c54c`) | Ask-mode REAL loop (fs-probe workflow, plan-anchored) parks a tool-grain gate → **postgres** inbox intent visible to **two client views over the new `/gw/loopjob/approvals/*` sub-lane** (filtered and unfiltered), non-owner sees nothing and gets **401** on decide, owner decides `once` → 200, engine resumes, fs-probe completes, loop `stopped|completed|1|1` with findings, durable job `completed` (att-2 succeeded), second decision **409**. Live-found fixes landed this leg: parked loops must not be fail-checked (`ad586546`), `_row_to_loop` now rehydrates `plan` (anchors vanished per checkpoint read — `7232c3cd`), and cross-server monitor instance-scoping for in-process loop liveness — foreign checkers (bdrt/ai4s/octopus) were killing a healthy rccs-hosted parked loop's attempts in a 3-way requeue cascade (`25645657`) | **VERIFIED LIVE** |
| WP-14 claim-side theft fix (2026-09-10, LIVE on shared umrs_db) | 0088 applied (schema v5 on umrs_db: `jobs.venue VARCHAR(32) NOT NULL DEFAULT ''` + index). Pre-venue rows `(625)` stay shared by design; per-site `JobStore.site_name` resolved from `site.yaml` at JobService startup; claims always enforce `venue='' OR venue=<site>` with spec-declared venue outranking site default. Live proofs: rccs-front's own bound job claims only on rccs-front, `bdrt-front claims on rccs-bound: []` (steal now impossible). Cluster servers restarted on the landings | **VERIFIED LIVE** |
| Cluster-venue hosting grid (WP-05 round 2, wp14 basis) | PUT/GET/POST \`loops/{id}/{state|iterations|snapshot}\` over the gateway; `knowledge/http_loop_store.py` adapter (driver-owned fields only); `knowledge/cluster_loop_entry.py` engine-only runner (agent admission refused with named reason); `deploy/rccs/loop-job.slurm` + `knowledge/_cluster_loop_job.sh` (job-private venue + secret hex handshake + allowed-hosts-capped guard + umrs source read-only mount); research_loop_backend routes venue=cluster:rccs accordingly | **VERIFIED LIVE — see leg below** |
| First real cluster-venue loop leg (2026-09-10, `cluster-v3-leg` via `job-cf065021fc94`) | Full chain VERIFIED: venue-normalized claim (jobs row `rccs-front`, only umrs-server-rccs claimed — octopus never took it, WP-14 holds) → `ClusterSlurmSubmitter` → remote sbatch 381169 on `<compute-node>` → `loop-job.slurm` → `_cluster_loop_job.sh` venue (private netavark `/24`, per-job runroot, podman secret `type=env`) → driver pod (`cap-drop ALL`, no-new-privileges, read-only rootfs, `--user 0` for Lustre principal) → `cluster_loop_entry` → **sim engine ran 1 iteration, wrote state back through the new `/gw/loopjob/*` HMAC lane (umrs-strict-validated), loop record → `stopped|completed|1|1`, attempt `succeeded`, job `completed`**. Slurm-side exit `0:9` = post-completion scancel teardown (benign, see operator runbook §8). Live-found fixes landed: OrchestratorConfig import path, remote wrapper path from site.yaml, venue normalization warmup (2cbac37d), 15-min remote-driver checker grace, `R4_DIR` export, tmpfs /run, secret via `type=env`, umrs-src mount layout for PYTHONPATH, Lustre root-user driver, `http_loop_store` on stdlib urllib (no requests in r4-mcp), `/gw/loopjob/` gateway lane + characterization-table exception, LoopStore.get/FindingSink.durability on the adapter, engine daemon-thread wait (process must outlive the driver), `psycopg2`-free lazy `umrs.jobs` package exports (PEP 562). Fix-first-then-leg discipline with leg IDs: 381020–381169 | **VERIFIED LIVE** |
| ADR-092 slice 8 (2026-10-12, repo-side, `wip/a92` + umrs `wip/umrs-a92`, not merged) | The frontend venue also hosts one team plan run per session: `POST /umrs/traces/plan-runs/{run_id}/venue` records the run and submits a `frontend_session` job whose worker command is `r4 venue worker --plan-run <id>` (no chat snapshot required; everything else is this ADR's path: sealed sources, the ADR-062 start gate, the ADR-051 budget, the same provisioner, worker env and lease). P7 extended, no new credential: the rail gw admits the `traces` family for the session's own bound run's record verbs only, with a one-time in-process attestation so only that worker writes the run (the owner's JWT client is refused). P9's sibling for plan gates: `…/rail/plan-gates` raises/reads ADR-066 inbox intents (`companion_approval_inbox`), never decides. A worker reporting `done` ends its session (`plan_run_done`). Tests on FakePodman; see ADR-092's slice-8 block. | **Landed repo-side**; live leg pending (worker image rebuild: `umrs_front_venue_rebuild_worker=true`) |
| ADR-092 slice 8b (2026-10-12, repo-side, not merged) | `umrs-infer` gains the reserved `site` source (fixed UUID in the lease's `source_ids`; `--source site` at start, no pushed credential): requests under the sidecar's `site/` prefix are served from a READY/DEGRADED EndpointRegistry row of the lease's own project only, the model rewritten to its served name, inbound credentials stripped and the site's service key (`GATEWAY_LITELLM_SERVICE_KEY` / `LITELLM_MASTER_KEY` in `umrs-infer`'s env) injected; model verbs only; a public-passthrough row is refused for a sensitive project (ADR-062). The internal listener is reachable only via a registry row; the sealed-source path and its public-host pin are unchanged. Needs `UMRS_REGISTRY_BACKEND=postgres` + `UMRS_REGISTRY_POSTGRES_DSN` in `umrs-infer`. | **Landed repo-side**; live leg pending |
| I-series legs (2026-09-09/10 venue system leg on umrs-front) | **I-1 pass** (worker: no direct egress, external DNS dead, egress alias resolves, raw sockets forbidden, no ping) · **I-2 pass** (sidecar chain proven end-to-end: worker → nginx → umrs-infer with path tools logged (`infer_eval sid=… client=172.20.8.2 verdict=ok path=/v1/models`), unseal+inject reached upstream LiteLLM which honestly rejected the canary key — status/headers pass-through) · **I-3 pass** (stream=True single-chunk pass with content-type preserved) · **I-5 pass** (8 concurrent streams, no 5xx) · **I-6 pass** (`path_denied` 403) · **I-7 pass** (squid: pypi 200; 10.x / 169.254.x / host.containers.internal → 403) · **I-8 pass** (`web_policy=none`: no HTTPS_PROXY/HTTP_PROXY env, no web at all, no squid) · **I-9 pass** (unpush via public r4 → next proxied request 401 `lease_revoked`, audit rows complete) · **I-11 pass** (sweep tears down sessions with only-revoked leases; all canary resources cleaned) · **I-12** unit-pinned (`redirect_offhost`; path policy correctly denies the probe envelope, so live creation waits for a reachable 302-off-host flow) · **I-13 pass** (cap-drop ALL, no raw sockets, ping absence) · **I-14 pass** (worker on non-internal net exits 78 `venue_isolation_failed`) · canary 0-hit sweep round 2 across 14 containers' logs, `.env`, sessions tree: clean everywhere; canary keys only ever in AEAD ciphertext (tombstoned). The leg itself found/fixed: preserve-proxied-URI recipe in the nginx template (`proxy_pass $infer` silently dropped request paths), netavark host-gateway semantics (`169.254.1.2` slirp address for bridge services), bridge-only peer binding for lease IP check, umrs-infer must be bridge-native (no rootless `network host`), sweeper must also tear down revoked sessions, row-wise `'path_denied'` first shows policies deny before upstream (good gate) | **VERIFIED LIVE, complete leg** |

## Amendment 2 (2026-09-21) — frontend session runtime = the sandbox worker

**Decision.** A `venue=frontend` companion session executes its turns in
the ADR-046 sandbox worker (podman, per-session `--internal` network,
egress sidecar, lease token) — never in the in-process
`CompanionWorkerSupervisor` / `companion_turn_executor` path, which uses the
site LiteLLM service key and inherits the server environment (venue plan
§4.3 round-1 wording is superseded by this amendment). Consequences and
the closed gap inventory (G-RS-01…29) are in
remote-session-tui-wave-2026-09-21.md.

Normative additions (P7–P10):

P7. **Worker principal rail.** The sandbox reaches its own journal only
through the sidecar's rail listener (`:8081`, path-anchored to
`/umrs/companion/sessions/<sid>/rail/`, gateway identity headers cleared)
and the API authenticates the rail by the lease token alone
(`umrs/api/_rail_identity.py`: valid · scope `inference` · `session_id`
match · optional egress-IP match). User JWTs never reach rail routes; lease
tokens never authenticate owner routes.

P8. **Turn transport = the F9 owner-scope queue + journal.** The client
appends, the worker pops through the rail, runs the turn with the sidecar
as its LLM route (api key = session token), mirrors the snapshot through
the rail, and reports `runtime` (phase, turn_seq, partial text) on
heartbeats that also renew the lease. The client adopts the journal copy
(server wins for `frontend`). No new inbound path into the sandbox.

P9. **Tool approvals = the T4 durable inbox** (`chat_turn_approvals`),
raised by the worker through the rail and answered by the owner from any
client. Timeout = deny.

P10. **One task table (revised 2026-09-21).** The session runtime
lifecycle is a durable job: `POST …/venue/start` submits ONE `jobs` row
of `job_type = frontend_session` (venue = the receiving site, one attempt
= the sandbox's life, `restart_policy = never`) and answers the
`provisioning` row; the durable runtime provisions on claim
(`umrs/jobs/frontend_session_backend.py`) and its status checker is the
lease authority (revoked/expired → succeeded `lease_ended`, idle beyond
`UMRS_VENUE_IDLE_SECONDS` → revoke + succeeded `idle`, worker `exited` →
failed). Stop tears down directly and cancels the job; a durable cancel
(`POST /umrs/jobs/{id}/cancel`, any surface) tears the sandbox down via
the cancel bus. `companion_venue_sessions` (0099 + 0100 `job_id`) stays
the session-keyed projection the TUI reads: state, lease/egress binding,
worker-reported runtime, plus `job` (status + attempt detail) on
`GET …/venue`. Jobs a sandbox creates through the rail gw carry
`spec.origin = {session_id, venue: frontend}` (lineage; no cascade —
workflows outlive the session that started them). Session units count in
the ADR-051 spawn budget (`AGENT_JOB_KINDS`). The private sweeper
(`venue_sweeper`, `session_venue_runtime`, `UMRS_VENUE_SWEEP_*`) is
retired.

## Consequences

- **Push cost:** every public source the user wants remotely must be
  pushed once (explicit, fingerprint-confirmed). Local `sources.json` is
  untouched by this ADR (G-1/G-2 hygiene already landed).
- **Attack surface shift:** the inference proxy is the highest-value
  component (holds the unseal key). Minimal code, read-only fs, no shell,
  own DB role; unseal-rate alerting named for R2; KMS unseal for R3.
- **Audit coverage:** per-request infer/deny rows in `secret_audit`;
  push/unpush rows in `user_source_audit`; canary sweep pins the 0-hit
  rule per release.

## Alternatives considered

- Whole-`sources.json` sync — plaintext creds on server, no revocation/audit (rejected).
- Tunnel back to laptop — kills disconnect resilience (rejected).
- Broker-as-key-vault (hash-only reuse) — cannot return a user-provided key later (rejected; broker stays the session-token issuer).
- Single-class egress (only inference) — breaks package/git tooling that agents need (rejected: two classes, D-4).
