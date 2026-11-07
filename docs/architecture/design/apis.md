# Design: APIs

> **Updated 2026-09-22 (design reset)**; §2–§3 corrected 2026-10-18
> (hardening H-10.1 follow-up: the `pipelines` and `verification` families
> and the `/gw/pipelines` locations are deleted; `buckets` and
> `scientific-catalogs` were missing from the `v1beta` row)
> **Audience:** Developers, architects, contributors
> **Status:** CANONICAL API reference — the surfaces that exist today,
> verified against `umrs/src/umrs/api/*.py` (router prefixes),
> `umrs/src/umrs/server/http_sidecar.py` (what is mounted),
> `umrs/src/umrs/shared/lanes.py` (lanes) and
> `gateway/nginx/conf.d/server.conf` (the edge route table) on the date
> above. The design target is one versioned public R4–UMRS protocol over
> ZeroMQ (gap rows D-01, D-04 in
> [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)).
> Command-level detail: [CLI reference](../../user-manual/13-cli-reference.md),
> [SDK reference](../../user-manual/14-sdk-reference.md).

---

## 1. Surfaces today

| Surface | Protocol | Entry | Auth |
|---|---|---|---|
| Edge | HTTPS + JWT | `/gw/<family>/*` on the NGINX + auth-sidecar edge (Apache 443 in front on umrs-front) | `auth_request /auth/validate` on every route |
| UMRS HTTP sidecar | HTTP (internal) | `/umrs/<family>/*`, 42 families in three lanes | `X-User-ID` + `X-Verified-*` with HMAC (strict mode) |
| UMRS ZeroMQ | string commands, CURVE + ZAP in `session` mode | control socket + per-user data sockets | JWT in the command; session-CURVE key |
| Bridge | JSONL JSON-RPC, protocol v1 | console ↔ companion, local | none (local process) |
| ACP | JSON-RPC (custom vocabulary) | `r4 acp` for editors | local (ADR-042) |
| Worker rail | HTTP, lease token | `/umrs/companion/sessions/{sid}/rail/*` | `_rail_identity.py` |
| Inference proxy | HTTP(S), session token | frontend venue → provider | lease + sealed source (ADR-046) |
| Inside a mini-RiVault | HTTP | LiteLLM `/v1/*`, vLLM, BentoML vision, RAG service | LiteLLM service key |

---

## 2. Edge route table (`gateway/nginx/conf.d/server.conf`)

Every `/gw/<family>/` block rewrites to `/umrs/<family>/` and forwards the
verified identity headers; rate zones are `llm_limit` (10 r/s),
`upload_limit` (5 r/s), `general_limit` (50 r/s) from `gateway/nginx/nginx.conf`,
plus `health_backend` (5 r/s, burst 10, one bucket per gateway) for
`/health/backend` only (2026-10-30).

| Edge location | Target / behaviour |
|---|---|
| `/health` | the gateway's own liveness: static 200, never asks the backend (container HEALTHCHECK, k8s liveness, the converge's gateway-start wait) |
| `/health/backend` | backend-aware probe: 200 only when this gateway's UMRS answers its `/health` (2 s timeouts), else a fixed 503; the backend's body is not relayed; rate-limited (`health_backend`, a JSON 429 past the burst). What "is the platform serving" probes use: `scripts/e2e-canary.sh`, the converge's "Platform serving" tasks, the demo-suite preflight |
| `/health/ready` | the auth sidecar's own `/health` |
| `/ready` | aggregate readiness: 200 only when UMRS `/readiness` (required stores) answers 200, via the sidecar's `/ready-aggregate` |
| `/saml/`, `/auth/cli-callback`, `/auth/web-login` | login flows (no `auth_request`) |
| `/gw/llm/` | `/umrs/llm/` — OpenAI-compatible proxy, SSE, 600 s read timeout |
| `/gw/chat/` | `/umrs/chat/` — Assistant Service (ADR-012) |
| `/gw/companion/` | `/umrs/companion/` — sessions, turns, approvals, trust, venue |
| `/gw/rag/`, `/gw/components/` | RAG proxy, component registry |
| `/gw/mcp/`, `/gw/skills`, `/gw/endpoints`, `/gw/jobs`, `/gw/models`, `/gw/workflows` | the corresponding UMRS families |
| `/gw/artifacts`, `/gw/provenance`, `/gw/a2l/`, `/gw/traces`, `/gw/usage`, `/gw/assets`, `/gw/catalog/`, `/gw/vision/`, `/gw/batch/` | catalog, lineage, usage, vision families |
| `/gw/knowledge/`, `/gw/knowledge/ingest/` (→ `/umrs/k2/`; `/gw/knowledge/k2` 308-redirects), `/gw/research/`, `/gw/loops/` | knowledge, research sessions, loops |
| `/gw/loopjob/`, `/gw/loopjob/approvals/`, `/gw/loopjob/workflows/` | cluster-hosted loop lane: pre-signed `X-Verified-HMAC`, no JWT |
| `/gw/grants/`, `/gw/projects/`, `/gw/circles/`, `/gw/quota/`, `/gw/sites/` (the caller's own view of this site, multi-HPC S2, 2026-10-24), `/gw/user-sources/`, `/gw/secrets/` | policy and identity families |
| `/gw/ops/ping`, `/gw/ops/connect-info`, `/gw/ops/` | client discovery (connect-info also carries the `s3cfg`, D-16) |
| `/gw/zmq/`, `/umrs/zmq/` (raw passthrough) | session-CURVE bootstrap; marked `X-API-Lane: internal`, `Deprecation: true` |
| `/gw/files/upload` | `/umrs/files/upload`, `client_max_body_size 0` |
| `/gw/files/download/` | `/umrs/files/download/`, served through `/internal/files/` or `/internal/s3/` (X-Accel, presigned URL never exposed) |
| `/gw/stream/`, `/gw/web/dashboard/`, `/gw/egress/` | streaming, web dashboard, egress-audit sink |
| `/gw/v1/(endpoints|models|llm)`, `/gw/v1beta/(knowledge|k2|loops|research)` | lane-prefixed aliases; bare `/gw/v1/`, `/gw/v1beta/` return 404 |

`tests/test_gateway_route_characterization.py` asserts every `/gw/*`
location authenticates except the documented probes and that rewrites match
their targets.

*2026-10-30 (issue #634): the health rows were one line, "edge liveness;
aggregate readiness via the sidecar", and `/health/ready` is not the
aggregate (it is the sidecar's own liveness). On 2026-10-28 all four public
`/<cluster>/health` answered 200 while three `umrs-server-<cluster>`
containers sat in Created, because `/health` is a static nginx answer. Added
`/health/backend` rather than making `/health` ask the backend: `/health`
is the liveness signal of the nginx container (HEALTHCHECK, k8s
`livenessProbe`, the converge's gateway-start wait), and a liveness probe
that follows the backend restarts a healthy gateway during every backend
outage. `/ready` already asks UMRS `/readiness`, but behind the auth sidecar
(it fails when the sidecar is down, not only the backend) and it relays the
sidecar's readiness body; `/health/backend` asks only the backend's own
liveness and answers fixed JSON, so the anonymous public URL does not
publish the backend's component summary or site capabilities. Tests:
`gateway/tests/test_nginx_config.py::test_health_backend_probes_umrs_and_fails_closed`,
`gateway/tests/test_nginx_audit_runtime.py::test_health_backend_*` (podman),
`tests/test_umrs_front_backend_probe.py`,
`tests/test_converge_workflow_contract.py::test_canary_*backend*`.*

---

## 3. UMRS families by lane (`umrs/src/umrs/shared/lanes.py`)

| Lane | Families (prefix `/umrs/…`) |
|---|---|
| `v1-stable` (6) | `endpoints`, `models`, `llm`, `jobs`, `workflows`, `files` |
| `v1beta` (30) | `knowledge`, `k2`, `loops`, `research`, `grants`, `user-sources`, `projects`, `buckets`, `quota`, `circles`, `scientific-catalogs`, `mcp`, `rag`, `uploads`, `companion`, `chat`, `catalog`, `artifacts`, `assets`, `provenance`, `traces`, `evidence`, `experiment-registry`, `scientific-memory`, `reflective-memory`, `vision`, `a2l`, `components`, `usage`, `web` |
| `internal` (5) | `ops`, `secrets`, `skills`, `zmq`, `egress` |
| dormant | `context` — router exists, never mounted (ADR-056) |

`umrs/tests/test_surface_characterization.py` fails if a served family has
no lane or a declared family serves nothing. Several families are served by
more than one router module (`knowledge` by `knowledge.py`,
`knowledge_graphs.py`, `knowledge_proposals.py`, `knowledge_query.py`,
`knowledge_upload.py`, `knowledge_lineage.py`, `knowledge_attrition.py`,
`bundle_restore.py`, `knowledge/events_sse.py`; `companion` by
`companion.py`, `companion_sessions.py`, `companion_chat_approvals.py`,
`companion_venue.py`; `components` by
`pipelines.py`).

---

## 4. Selected routes (verified)

| Family | Routes |
|---|---|
| `/umrs/llm` | `POST /v1/chat/completions`, `POST /v1/completions`, `GET /v1/models`, `POST /v1/embeddings` |
| `/umrs/chat` | `POST /v1/chat/completions`, `GET /v1/models`, `GET /manifest` |
| `/umrs/endpoints` | `GET /active`, `POST /provision`, `GET /{job_id}`, `GET /{job_id}/status`, `POST /{job_id}/refresh`, `POST /{job_id}/deprovision`, `PUT /{job_id}/upgrade`, `GET /{job_id}/location`, `GET /{job_id}/metrics`, `GET /stats`, `GET /health` |
| `/umrs/models` | `GET`, `POST`, `GET /{model_id}`, `PATCH /{model_id}`, `DELETE /{model_id}`, `POST /{model_id}/detect`, `GET /{model_id}/path`, `GET /system`, `GET|PATCH /{model_name}/policy` |
| `/umrs/jobs` | `POST` (idempotency key, 409 on repeat), `GET /{job_id}`, `GET /{job_id}/manifest[s]`, `POST /{job_id}/cancel`, `POST /{job_id}/retry`, `POST /{job_id}/reproduce`, `POST /{job_id}/attempts/{attempt_id}/credentials[/sts]`, `POST /{job_id}/attempts/{attempt_id}/presign`, `GET /stats` |
| `/umrs/workflows` | `GET`, `POST` (upload), `GET|DELETE /{workflow_name}`, `POST /{workflow_name}/trigger`, `GET /{workflow_name}/triggers`, `PUT|GET|DELETE /run-definitions[/{name}]`, `POST /run-definitions/{name}/trigger`, `GET /executions/{id}[/results]`, `POST /executions/{id}/cancel`, `POST /executions/{id}/steps/{step}/approve` |
| `/umrs/files` | `POST /upload`, `POST /presign`, `POST /presign/batch`, `GET /{file_id}/metadata`, `DELETE /{file_id}`, `GET /download/{path}`, `GET /exists`, `GET /download-status` |
| `/umrs/uploads` | `POST /manifests`, `GET /manifests/{id}`, `POST /manifests/{id}/parts/{n}`, `POST /manifests/{id}/complete` |
| `/umrs/knowledge` | `POST /upload` (202, submits `knowledge_ingest`), `POST /query`, `GET /search`, `POST /external/search`, `GET /objects[/{id}]`, `GET /graph`, `GET /graph-pg`, `GET /graph/{id}/neighbors`, `POST /correction`, `GET /readiness`, `GET /dashboard`; graphs: `GET|POST /graphs`, `GET|PATCH /graphs/{graph_id}`, `GET /graphs/{graph_id}/content`; proposals: `POST|GET /graphs/{graph_id}/proposals`, `GET /proposals/{id}`, `POST /proposals/{id}/decision` |
| `/umrs/k2` | durable knowledge jobs (ingest, verify), artifact listing, lint |
| `/umrs/companion` | `GET|POST /workers`, `POST /chat`, `GET /turns/{attempt_id}[/events]`, `GET /approvals/pending`, `POST /approvals/{id}/decision`, `GET|POST /trust`, `POST /trust/check`, `DELETE /trust/{tool}`, `POST /sessions/{sid}/archive`; sessions: `GET|POST /sessions/{sid}/snapshot`, `/turns`, `/queue`, `/turns/{turn_id}`; venue: `POST /sessions/{sid}/venue/start`, `GET /sessions/{sid}/venue`, `POST /sessions/{sid}/venue/stop`, rail `GET|POST /sessions/{sid}/rail/snapshot`, `POST …/rail/queue/pop`, `POST …/rail/heartbeat`, `POST …/rail/approvals`, `GET …/rail/approvals/{id}` |
| `/umrs/loops` | `POST`, `GET /{loop_id}`, `GET|POST /{loop_id}/iterations`, `GET /{loop_id}/snapshot`, `PUT /{loop_id}/state`, `POST /{loop_id}/stop|pause|resume` |
| `/umrs/mcp` | `GET|POST /servers`, `GET|DELETE /servers/{id_or_name}`, `GET /tools`, `GET /tools/lookup`, `GET /health`, `POST /call` (alias `/invoke`) |
| `/umrs/catalog` | `GET /search`, `GET /read`, `GET /stats` |
| `/umrs/rag` | `POST /query`, `POST /upload`, `GET /documents[/{id}]`, `DELETE /documents/{id}`, `GET /stats` |
| `/umrs/projects` | `POST` (idempotent ensure), `GET`, `GET|PATCH /{project_id}` |
| `/umrs/skills` | `GET`, `GET /search`, `POST`, `GET|DELETE /{id_or_name}` |
| `/umrs/zmq` | `GET /server-key`, `POST|GET|DELETE /session[/{session_id}]`, `POST /session/{session_id}/refresh` |

FastAPI `/docs` and `/redoc` are served only when `GATEWAY_ENVIRONMENT`
is development or test.

---

## 5. Identity and conventions

- **Headers.** The sidecar sets `X-User-ID`, `X-Project-ID`,
  `X-Verified-Project-ID`, `X-Verified-Is-Admin`, `X-Verified-Workflow-Author`,
  `X-Verified-Project-Memberships` and signs them as `X-Verified-HMAC`
  (`r4-contracts/src/r4_contracts/handshake_hmac.py`); UMRS strict mode
  honours `X-Verified-*` only with a valid signature
  (`umrs/src/umrs/api/_verified_headers.py`). Clients never set these.
- **Agent marker.** Companion calls carry `x-r4-actor: agent` and
  `x-r4-session-id`; graph mutations from an agent need an approved
  proposal (`umrs/src/umrs/api/_knowledge_mutation_guard.py`).
- **Correlation.** `X-Request-ID` from NGINX or `X-Correlation-ID` from the
  SDK (`umrs/src/umrs/server/correlation.py`).
- **Errors.** Every `HTTPException` is projected to a structured body with
  `error_id` and `request_id` (`umrs/src/umrs/api/error_responses.py`);
  typed platform errors add the `x-platform-error-code` header
  (`typed_errors.py`); RFC 7807 problem+json is an opt-in projection
  (`problem_json.py`, `UMRS_PROBLEM_JSON`).
- **Idempotency.** `POST /umrs/jobs` accepts `idempotency_key` and answers
  409 on a repeat (`umrs/src/umrs/api/jobs.py`).
- **Streaming.** Chat completions stream as SSE end to end (`proxy_buffering
  off` at the edge); knowledge events at `/umrs/knowledge` SSE
  (`umrs/src/umrs/knowledge/events_sse.py`).
- **Scopes.** One grammar everywhere: `system`, `project:<id>`,
  `circle:<project>/<id>`, `user:<id>`, `session:<id>`
  (`group:<id>` parses, admits no one — superseded, ADR-086)
  (`umrs/src/umrs/shared/scope_grammar.py`).

---

## 6. ZeroMQ string-command contract

Defined in [umrs/doc/zmq_api_contract.md](../../../umrs/doc/zmq_api_contract.md);
implemented by `umrs/src/umrs/core/MessageQueue.py` (server) and
`r4/src/r4/umrs_transport.py` (client).

- Control channel `DEALER → ROUTER`; message form `CMD:client_id:payload`.
- Commands: `HAND_CHECK` / `READY?` → `READY!`; `CREATE_DATA_QUEUE:<user>` →
  `tcp://<host>:<port>` (per-user `PUSH → PULL` data socket);
  `FILE:<user>:<count>` and `OBJECT:<user>:<count>` → `ACKNOWLEDGE` |
  `NO_DATA_QUEUE`; `STATUS`, `RECOMPUTE` / `UPDATE` / `CHECK` / `RELOAD`,
  `WAIT_OUTPUT`, `EXPORT_EMBEDDINGS` (rows returned inline; the client never
  touches the database).
- Ports: `UMRS_CONTROL_PORT` (default 5555) and `UMRS_DATA_PORT_RANGE`;
  on umrs-front one range per cluster stack (rccs 50090 / 50091–50098,
  ai4s 50100 / 50101–50108, bdrt 50110 / 50111–50118, octopus 50120 /
  50121–50128).
- Security: `UMRS_ZMQ_AUTH_TYPE = none | curve | session`; in `session`
  mode the ZAP authenticator consults the `SessionCredentialsProvider`
  populated by `POST /umrs/zmq/session`.
- Routing: `r4/src/r4/config.py` decides per operation
  (`OPERATION_ZMQ_ELIGIBLE`: `status`, `model_list`, `model_register`,
  `model_download`, `job_list`, `job_submit`, `job_cancel`,
  `workflow_list`, `workflow_run`, `workflow_upload`, `export_embeddings`,
  `file_upload`, `file_download`; `OPERATION_GATEWAY_ONLY`: `chat`,
  `chat_completion`, `mcp_list_tools`, `mcp_call_tool`,
  `mcp_register_server`), with `R4_TRANSPORT_MODE = gateway | direct | auto`
  and HTTP fallback in `r4/src/r4/_transport_router.py`.

Protocol strings are stable but unversioned; there is no handshake
version field and no wire-contract test on both sides (D-04).

---

## 7. Bridge protocol v1 and ACP

- The console speaks JSONL JSON-RPC to the companion's bridge server;
  both sides pin `BRIDGE_PROTOCOL_VERSION = 1`
  (`packages/r4-tui-v3/src/bridge/jsonl-client.ts`,
  `r4/src/r4/companion/ui/bridge_server.py`) and a mismatch is a named
  error. Reconnect fails pending requests and never replays mutating
  commands.
- Editors attach through `r4 acp` (`r4/src/r4/acp.py`), a custom JSON-RPC
  vocabulary named as such (ADR-042).

---

## 8. Client SDK and CLI (verified shapes)

```python
from r4 import Client

client = Client()
client.chat("Hello")                       # or client.chat_stream(...)
client.provision_endpoint(model="…")
client.jobs, client.workflows, client.data, client.rag, client.mcp, client.vision, client.knowledge
```

```bash
r4 console                                  # primary surface
r4 project create / import / verify / loop  # projects and ingestion
r4 endpoint up --model <name> ; r4 model list
r4 workflow …  ; r4 job status <id>
r4 mcp list | register | info | health
r4 mcp hatch create | install | search | connect | list | share
r4 venue …  ; r4 auth login
```

Sub-clients are properties of `r4/src/r4/client.py`; command modules live
under `r4/src/r4/_cli/`.

---

## 9. Design target

The design (suggestion 5) treats the R4–UMRS message schema as a public,
versioned API so that R4 and UMRS evolve separately and other clients (web
UI, notebooks) become possible. Today there are three unversioned contracts
(HTTP families, ZeroMQ strings, bridge v1). Closing this is D-04, and it
depends on the transport/edge decision D-01/D-02: either a ZeroMQ RPC
surface for the control-plane families or a versioned HTTPS API terminated
by UMRS. Until then new client features use the HTTP families and new data
movement uses ZeroMQ, exactly as above. (2026-10-18: both are settled —
ADR-059 kept the two lanes (D-01/D-02 Implemented by design amendment) and
protocol v1.0 covers both lanes, [R4-UMRS-PROTOCOL.md](../../R4-UMRS-PROTOCOL.md),
D-04 Implemented.)

---

## Related documents

| Document | Purpose |
|---|---|
| [CLI reference](../../user-manual/13-cli-reference.md) | Full CLI command reference |
| [SDK reference](../../user-manual/14-sdk-reference.md) | Python SDK reference |
| [zmq_api_contract.md](../../../umrs/doc/zmq_api_contract.md) | ZeroMQ contract (UMRS submodule) |
| [gateway README](../../../gateway/README.md) | The edge |
| [architecture.md](architecture.md) | Target architecture and implementation map |
| [security.md](security.md) | Identity and enforcement per layer |
| [dataflow.md](dataflow.md) | Flows that use these routes |
| [TRANSPORT_PARITY_MATRIX.md](../runtime/TRANSPORT_PARITY_MATRIX.md) | HTTP vs ZeroMQ parity (pre-reset draft) |
