# Design: Components

> **Updated 2026-09-22 (design reset)**
> **Audience:** Developers, architects, contributors
> **Status:** CANONICAL component catalog, organised by the four pillars of
> [R4-UMRS-DESIGN.md](../../R4-UMRS-DESIGN.md) plus the edge that exists
> today and the shared contracts. Every entry was verified by opening its
> directory on the date above; "status vs design" cites rows of
> [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../../R4-UMRS-GAP-ANALYSIS-2026-09-22.md).

Each entry gives: **purpose · key modules · owner · boundary rule · status
vs design**. Package-level import rules are in
[CONSTITUTION.md §1](../../../CONSTITUTION.md) and enforced by
`tools/import_boundary_validator.py`.

---

## 1. R4 — the researcher-facing layer (`r4/`, `packages/`)

### r4 console (TUI)

- **Purpose:** the primary surface; the design's single entry point.
- **Key modules:** `packages/r4-tui-v3/` (Bun + OpenTUI); routes in `src/routes/`: `work`, `tasks`, `knowledge`, `catalog`, `sessions`, `loop`, `research`, `code`, `infrastructure`, `settings`; `src/bridge/` (JSONL client, session / venue / plan / approval / catalog bridges).
- **Owner:** R4.
- **Boundary:** speaks only bridge protocol v1 to the companion (`BRIDGE_PROTOCOL_VERSION = 1` on both sides: `src/bridge/jsonl-client.ts`, `r4/src/r4/companion/ui/bridge_server.py`); never reaches UMRS itself.
- **Status:** primary surface; other surfaces still shipped (D-25).

### r4 CLI and SDK

- **Purpose:** scripting surface and the Python client library.
- **Key modules:** `r4/src/r4/cli.py`, `_cli/` (69 modules), `client.py` (sub-clients `data`, `workflows`, `jobs`, `rag`, `mcp`, `vision`, `knowledge`; `chat`, `chat_stream`, `provision_endpoint`), `config.py` (`OPERATION_ZMQ_ELIGIBLE`, `OPERATION_GATEWAY_ONLY`, `R4_TRANSPORT_MODE`), `_transport_router.py`, `umrs_transport.py` (ZeroMQ), `curve_session.py`, `auth.py`, `oidc.py`, `jupyter/`.
- **Owner:** R4.
- **Boundary:** never imports `umrs`; consumes `r4-contracts`; keeps no durable state, job store, workflow registry or storage credential (CONSTITUTION §1.3, §6).
- **Status:** dual transport (D-01); `_cli/login.py` still writes a per-user `s3cfg` (D-16); `providers/` can call public LLM APIs directly (D-37, D-51).

### Companion (agent harness)

- **Purpose:** the design's agent harness: tool calling, planning, execution, error recovery, sub-agents, permission ladder, context control.
- **Key modules:** `r4/src/r4/companion/` — `agent.py`, `tools.py`, `permissions.py` (`ASK_ALWAYS` / `AUTO_SAFE` / `TRUST`, never-auto tools, taint fence), `taint.py`, `context.py`, `context_budget.py`, `skills.py`, `session*.py` (SQLite store, Postgres mirror), `harness/` (`probe`, `guides`, `sensors`, `progress`, `steering`, `templates`), `knowledge_tools.py`, `knowledge_graph_tools.py` (`knowledge_graph_propose` / `knowledge_graph_decide`), `knowledge_write_gate.py`, `science_loop.py`, `ui/` (`bridge_server.py`, `repl.py`), `acp_session.py`; `r4/src/r4/acp.py` (ADR-042).
- **Owner:** R4.
- **Boundary:** authorisation decisions consult the server-side trust allowlist (ADR-035); knowledge mutation goes through proposals (migration 0069).
- **Status:** implemented (D-26, D-28); chat-turn authority split between SQLite and Postgres (D-30, ADR-057).

### Skills

- **Key modules:** `r4/src/r4/companion/builtin/` (27 skill groups in Agent Skills `SKILL.md` form), `r4/src/r4/skills.py`, `skill_lockfile.py`, `skill_scanner.py`; user and project skill directories.
- **Owner:** R4 (authoring, loading); UMRS registry `umrs/src/umrs/skills/` at `/umrs/skills`.
- **Status:** implemented; not part of the catalog bundle, no per-skill tests (D-27).

### Multi-agent teams

- **Key modules:** `packages/r4-teamengine-spi/` (ADR-020: `TeamEngine`, `RuntimeServices` brokers, plugin loader), `r4/src/r4/teams/` (`native.py`, `brokers.py`, `registry.py`, `resolver.py`, `service.py`), `engines/r4-engine-langgraph/`; older `r4/src/r4/teamup/` (roles, crews, comms, budget) and `r4/src/r4/research_loop/team.py`; companion sub-agents.
- **Owner:** R4.
- **Boundary:** the SPI is the designed seam; adding another engine is forbidden (CONSTITUTION §1.6).
- **Status:** fragmented (D-29).

### Research loop (client side)

- **Key modules:** `r4/src/r4/research_loop/` (`orchestrator.py`, `state.py`, `publish.py`, `team.py`, `agent_exec.py`, `bridges.py`, `domain_skills/`), `r4/src/r4/agent/` (`research_director.py`, `belief_state.py`, `research_orchestrator.py`, …), `companion/science_loop.py`, `companion/research_loop_tools.py`.
- **Owner:** R4 for the "ask the scientist" surface and loop control; the engine itself is UMRS-side (§2).
- **Status:** fragmented; report from loop state, not a catalog-grounded paper (D-32, D-45).

### Remote sessions (client side)

- **Key modules:** `r4/src/r4/venue.py`, `venue_rail.py`, `venue_worker.py` (`r4 venue worker`: isolation self-test, snapshot pull through the rail, queued turns, heartbeats, durable approvals).
- **Boundary:** the worker never holds a user JWT or a provider key (ADR-046).
- **Status:** frontend venue only (D-09, D-48).

### Providers and integrations

- **Key modules:** `r4/src/r4/providers/` (`openai_compat`, `bedrock`, `ollama`, `litellm`, `router`, `factory`), `r4/src/r4/integrations/` (`hatchling.py`, `hatch_runtime.py`, `langchain.py`, `llamaindex.py`, `mcp_use.py`), `r4/src/r4/mcp.py`.
- **Status:** Hatch tooling implemented (D-20); public-provider routing is a policy gap (D-37, D-51).

### Secondary surfaces

`packages/r4-web/` (React web console), `vscode-extension/`, `r4/src/r4/jupyter/`, `mcp-apps/` (MCP UI apps), `r4/src/r4/tui/` (Python helpers used by the bridge; the interactive TUI is the console above). Status: to be demoted or retired deliberately (D-25).

---

## 2. UMRS — the infrastructure backbone (`umrs/src/umrs/`)

### Server core

- **Purpose:** parent process of everything UMRS launches; both transports.
- **Key modules:** `umrs/src/server_umrs.py`; `core/` (`MessageQueue.py` control + per-user data sockets, `zmq_security.py` ZAP/CURVE, `session_curve_provider.py`, `TaskManager.py`, `thread_pool.py`); `server/` (`http_sidecar.py` explicit router registry and lifespan, `durable_job_runtime.py`, `commands/`, `otel.py`, `correlation.py`, `workflow_runner.py`, `step_router.py`, `web_gui/`).
- **Boundary:** foundation modules never import `umrs.api` or `umrs.server`; a router that is not mounted does not exist (ADR-053, ADR-056).
- **Status:** implemented; identity originates in the JWT, not a UMRS keystore (D-03).

### API families

- **Key modules:** `api/` — 42 mounted families classified by `shared/lanes.py` (`v1-stable` 7, `v1beta` 30, `internal` 5; `context` dormant). Identity helpers `_identity.py`, `_verified_headers.py` (HMAC on `X-Verified-*`), `_scope_identity.py`, `_rail_identity.py`; error shape `typed_errors.py`, `error_responses.py`, `problem_json.py`. Route tables: [apis.md](apis.md).
- **Status:** unversioned (D-04).

### Durable jobs

- **Key modules:** `jobs/` — `models.py` (`JobType`, `JobStatus`), `store.py` (advisory locks), `scheduler.py`, `reconciler.py` + `session_recovery.py` (LOST + requeue), `monitor.py`, `manifest.py`, `attempt_credentials.py`, `sts_federation.py`; backends `workflow_run.py`, `model_download.py`, `knowledge_adapters.py`, `data_ingestion.py`, `reindex.py`, `companion_worker_backend.py`, `frontend_session_backend.py`, `research_loop_backend.py`, `batch_infer.py`, `model_eval.py`, `fine_tune.py`; `vector_version_store.py`.
- **Owner:** UMRS (the only job store; CONSTITUTION §1.3).
- **Status:** implemented (ADR-002, ADR-044, ADR-047).

### Orchestrator

- **Key modules:** `orchestrator/` — `base.py`, `factory.py`, `slurm_podman_backend.py` (submits `serve-model.slurm`), `slurm_backend.py` + `launcher_stack.py` (Singularity, legacy), `remote_slurm_backend.py`, `kubernetes_backend.py`, `compose_backend.py`, `hybrid/`, `site_config.py` + `site_profiles/` (`compose-dev`, `k8s-cluster`, `lab-podman`, `rccs-cloud`), `deployment_spec.py` (tier `system | project | user`), `readiness.py` (`/v1/models` probe), `endpoint_registry.py`, `janitor.py`.
- **Boundary:** one `execution_authority` per job (ADR-004).
- **Status:** D-07 implemented; Singularity path still present (D-12).

### mini-RiVault runtime (compute sandbox)

- **Key modules:** `mini-rivault-llm/` — `serve-model.slurm`, `serve-model.yaml` (compose profiles `harness`, `vllm`, `vision-bentoml`, `vision`, `rag`, `egress`, `custom-mcp`, `sts-renewal`, `litellm-db`), `venue.sh` (ADR-045), `assert-offline.sh`, `attempt_budget.sh` (ADR-048), `mini-rivault-job.sh`, `userdata-access.yaml`, `sts_renewal.py`, `litellm_fs/`, `nginx_fs/`, `coredns_fs/`, `sts_fs/`.
- **Status:** compute sandbox exists; not universal and no named profile contract (D-12, D-13).

### Venues and the inference proxy (remote agent sessions)

- **Key modules:** `venues/` (`frontend_service.py`, `frontend_podman.py`, `frontend_config.py`, `egress_nft.py`, `venue_sessions.py`); `infer_proxy/` (`app.py`, `leases.py`, `policies.py`); `security/` (`seal.py`, `sealed_sources.py`, `provider_url.py`, `scanner.py`, `rotation.py`, `retention.py`); `api/companion_venue.py` (start / status / stop, worker rail; `RAIL_GW_FAMILIES = {"workflows", "jobs"}`).
- **Status:** landed repo-side 2026-09-21 (ADR-046 Amendment 2); rail cannot reach knowledge yet (D-42); no CPU-only venue (D-48).

### Models and inference

- **Key modules:** `model_registry/` (`store/`, `versions.py`, `certification.py`, `conversion.py`, `detect.py`, `capabilities.py`); `inference/` (`embeddings.py`, `sentence_transformers.py`, `masked_autoencoder.py`, `hmae/`, `resnet_50.py`, `sam3_segmentation.py`, `openai_api.py`); `vision_service/` (BentoML service, adapters, `Containerfile.vision`); `api/llm.py`, `api/models.py`, `api/endpoints.py`, `api/vision.py`, `api/vision_durable.py`.
- **Status:** LLM + vision implemented; no VLM endpoint class (D-10); three inference contracts (D-11).

### Knowledge spine

- **Key modules:** `knowledge/` (`ingest.py`, `ir.py`, `parse_code.py`, `parse_notebook.py`, `ocr.py`, `extract.py`, `ontology.py`, `ontology_registry.py`, `graph_registry.py`, `pg_graph_sink.py`, `verify.py` + `verifiers/` (ten verifiers), `bundle_export.py`, `bundle_import.py`, `planner.py`, `loop.py`, `loop_hosting.py`, `finding_ingest.py`, `tool_approvals.py`, `access_scope.py` (visibility `project | system | ambient`), `egress_fetch.py`); satellites `knowledge_graph/`, `knowledge_graph_api/`, `knowledge_model/`, `scientific_memory/`, `embeddings_api/`, `search_api/`, `evidence/`, `experiment_registry/`, `research_sessions/`; `api/knowledge*.py`, `api/rag_service.py`.
- **Boundary:** satellites are leaf stores; `knowledge/` composes them (ADR-024).
- **Status:** the catalog's parts without a catalog object (D-39 … D-46).

### Data catalog, assets, provenance

- **Key modules:** `data_catalog/` (`pg_store.py`, `ingest.py`, `extract.py`, `modality.py`, `intake_uploads.py`, …), `asset_index/`, `artifact_registry/`, `a2l/` (ADR-034); `api/catalog.py`, `api/assets.py`, `api/provenance.py`, `api/traces.py`, `api/a2l*.py`.
- **Status:** this is the *data* catalog (ADR-011), not the Scientific Catalog (D-39).

### Workflows and pipelines

- **Key modules:** `workflow/` (`models.py` schema `2.0.0`, `pg_registry.py`, `persistence.py`), `pipeline_sdk/` (`step.py`, `dag.py`, `builder.py`, `templates/`, `connectors/`), `api/workflows.py`, `api/pipelines.py`.
- **Status:** R4 authors, UMRS executes (ADR-021); no importers (D-08).

### Accounts, policy, quotas

- **Key modules:** `admin/user_store.py` (JSON records under `user_files/`), `shared/auth.py`, `api/projects.py` (migration 0095), `api/groups.py` (0097), `api/agent_quota.py`, `api/quota_status.py`, `api/disk_ledger.py` (0096), `companion_trust/` (`store.py`, `approval_inbox.py`, `chat_turn_approvals.py`), `grants/` (asset and source grants), `shared/scope_grammar.py`, `tenants/` (experimental, unwired).
- **Status:** partial (D-06, D-36).

### MCP and skills registries (server side)

- **Key modules:** `mcp/` (`models.py`, `schema.py`, `store.py`), `api/mcp.py` (SSRF guard, `/call`), `api/mcp_service.py`; `skills/store.py`, `api/skills.py`.
- **Status:** no placement or trust-class field (D-19, D-24).

### Stores

- **Key modules:** `migrations/` (`0001` … `0100`, 69 files, `__main__.py` runner); `shared/storage/` (`s3.py` with `S3_BACKEND`, `minio.py`, `user_s3cfg.py`); `shared/pg*.py`, `shared/pgvector_index.py`.
- **Status:** SeaweedFS on umrs-front, MinIO elsewhere (D-17); `s3cfg` delivery (D-16).

### Operator surfaces

`tui/`, `tui_v2/` (Textual monitor for `umrs-server`), `api/web_dashboard.py` + `server/web_gui/`, `api/ops.py`, `cli/`, `client/` (the `umrs` CLI), `admin/create_users.py`.

---

## 3. Scientific Catalog — parts that exist today

| Design component | Where it lives today | Gap |
|---|---|---|
| Source materials | S3 `knowledge-uploads/<project>/…` (`api/knowledge_upload.py`); `catalog_objects` rows (migration 0054) | raw objects not in the bundle (D-40) |
| RAG index + vector DB | `knowledge_node_embeddings` (0075), `memory_vectors` (0010); `api/rag_service.py`; `jobs/vector_version_store.py` | image partial, video absent (D-14, D-15) |
| Knowledge graph | `knowledge_nodes` / `knowledge_edges`, `knowledge_graphs` registry + proposals (0069), ontologies (0091), visibility (0092) | no proposed/established tiers or retraction (D-35) |
| Skills | `r4/src/r4/companion/builtin/`, user/project dirs, `umrs/src/umrs/skills/` | not in the bundle (D-27) |
| MCP server definitions | `umrs/src/umrs/mcp/` registry, `mcp-servers/hatch-registry/registry.json` | not in the bundle (D-23) |
| Metadata, version, export | `r4-kb` v1.2 (`knowledge/bundle_export.py`, HMAC-signed manifest; import accepts 1.0–1.2) | no catalog object, contributors, licence, redaction (D-39, D-40, D-41, D-46) |

---

## 4. Custom MCP servers (`mcp-servers/`)

- **Key modules:** `mcp-servers/src/r4_mcp/` — `servers/` (`filesystem`, `database`, `webfetch`, `code`, `platform`, `rag_proxy`, `vision`), `runners/custom_mcp.py` (manifest validation: `sandbox.network = egress_guard`, `filesystem = read_only`, positive `max_memory_mb` / `max_runtime_sec`) + `sandbox_enforcer.py`, `stdio_adapters/` (`frontend_default`, `hpc_job`, `slurm_status`, `science_mesh`, `registry`), `egress_guard.py` (deny-by-default forward proxy), `security.py`, `registry.py`, `chain_scanner.py`, `workflow_policy.py`; `hatch-registry/` (`registry.json`, packages `platform-tools`, `rag-search`, `knowledge-tools`, `rikyu-hpc`, `rikyu-docs`, `fugaku`, each `hatch.json` + `server.py` exposing `create_server()`).
- **Owner:** the group that wraps the tool; the platform ships the defaults (ADR-022, ADR-023).
- **Boundary:** agents reach registered servers through UMRS (`/umrs/mcp/call`); local Hatch packages run through the R4 bridge (`r4/src/r4/integrations/hatch_runtime.py`).
- **Status:** registry and tooling implemented; placement, lifecycle-as-job, trust classes and trusted-host venue absent (D-19 … D-24, D-49).

---

## 5. The edge today (`gateway/`) — not a design component

- **Purpose:** the NGINX + auth-sidecar edge: JWT validation on every `/gw/*` route, rate limits, X-Accel downloads, session-CURVE bootstrap, audit.
- **Key modules:** `gateway/nginx/` (`nginx.conf` rate zones `llm_limit` 10 r/s, `upload_limit` 5 r/s, `general_limit` 50 r/s; `conf.d/server.conf` route table), `gateway/sidecar/main.py` (`/validate`, `/validate-optional`, `/validate-download/{path}`, `/audit`, `/health`, `/ready-aggregate`, `/auth/cli-callback`, `/auth/web-login`; HS256 and JWKS/RS256; `gateway_audit_log`), `sidecar/saml_sp.py`, `sidecar/otel.py`, `gateway/k8s/`, `gateway/tests/`.
- **Boundary:** stateless proxy/policy layer only; imports neither `umrs` nor `r4` (CONSTITUTION §1.5).
- **Status:** UMRS's edge process by ADR-059 (2026-10-18: said "contradicts the design's single gateway (D-02); its future is decision D-01/D-02" — D-02 Implemented by design amendment 2026-09-22). `gateway/gateway/` holds only `__pycache__` — the Python gateway is gone (ADR-014).

---

## 6. Shared contracts, deployment, enforcement

### r4-contracts

- **Key modules:** `r4-contracts/src/r4_contracts/` — `a2l`, `agent_tool_playbook`, `components`, `dto`, `errors`, `extensibility`, `handshake_hmac`, `knowledge`, `mcp`, `provenance`, `recipes`, `research`, `scheduler`, `security`, `skills`, `spaces`, `taint`, `team_engine/`, `workflows`, `workspace`.
- **Boundary:** zero non-stdlib dependencies; imports nothing from `r4`, `umrs` or `gateway` (ADR-005; `tools/contracts_fence.py`).

### Deployment

- **Key modules:** `deploy/ansible/` — `bootstrap.sh --profile <name>`, inventories `umrs-front`, `rccs-cloud`, `bdrt`, `octopus`, `atom-k8s`; roles `common`, `compute_node`, `front_end`, `github-actions-runner`, `k8s_node`, `login_node`, `r4_web`, `tunnel`; playbooks per profile plus `remote-tunnel.yaml`, `slurm-delegation.yaml`.
- **Rule:** Ansible only, host checkouts are read-only deploy inputs (ADR-019 amendment).
- **Status:** one UMRS stack per cluster on umrs-front (D-47).

### Enforcement tools

`tools/enforcement_maturity_scorer.py` registers 24 validators:
`import_boundary_validator`, `test_import_hygiene_validator`,
`complexity_baseline`, `lint_baseline`, `contracts_fence`,
`dto_equivalence_check`, `schema_version_tracker`, `stale_name_validator`,
`config_drift_validator`, `data_authority_validator`,
`runtime_support_matrix`, `adr_index_uniqueness_validator`,
`exemption_governance_validator`, `traceability_validator`,
`orphan_module_detector`, `adr_linkage_validator`, `bridge_detector`,
`dead_compat_bridge_validator`, `check_metrics_coverage`,
`cleanup_readiness_report`, `transport_boundary_audit`,
`compat_retirement_tracker`, `security_compat_scanner`,
`tool_route_liveness`. The doc gate is `tools/doc_sanity_gate.py`; the
local pre-commit gate is `scripts/ci-lint-local.sh`.

---

## 7. History

Removed 2026-09-11 (ADR-014): the Python gateway (`gateway/gateway/`), the
Ink TUI and the `r4 research` CLI; `sat_kg_*` tables dropped (migration
0089). The standalone A2L service became the bounded module `umrs/src/umrs/a2l/`
(ADR-034). MinIO on umrs-front was replaced by SeaweedFS on 2026-09-14
(ADR-055).

---

## Related documents

| Document | Purpose |
|---|---|
| [architecture.md](architecture.md) | Target architecture and implementation map |
| [security.md](security.md) | Security model, per layer |
| [dataflow.md](dataflow.md) | Target and current data flows |
| [apis.md](apis.md) | API surfaces |
| [glossary.md](glossary.md) | Canonical terms |
| [LIBRARY_GROUP_STRUCTURE.md](../boundaries/LIBRARY_GROUP_STRUCTURE.md) | Package boundary definitions (pre-reset) |
| [UMRS_CORE_RUNTIME_SPEC.md](../runtime/UMRS_CORE_RUNTIME_SPEC.md) | UMRS runtime ownership (pre-reset, 2026-05) |
| [R4-UMRS-PLATFORM-OVERVIEW.md](../../R4-UMRS-PLATFORM-OVERVIEW.md) | What runs today |
