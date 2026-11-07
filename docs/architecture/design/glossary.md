# Glossary — Canonical Terminology

> **Purpose:** One meaning per term across all documentation.
> **Last updated:** 2026-09-22 (rewritten from [the design](../../R4-UMRS-DESIGN.md); implementation-side terms kept where code still uses them).
> Naming decisions still pending (expansions of R4 and UMRS) are gap row D-53.

## Design terms

| Term | Meaning in this project | Today in the tree |
|------|-------------------------|-------------------|
| **R4** | The researcher-facing layer: console (TUI), agent harness, skills, context control, multi-agent teams, local/remote session control, project and workflow management, the autonomous research loop. | `r4/`, `packages/r4-tui-v3/`. The earlier tagline "___ are for ___" is legacy. |
| **UMRS** | The infrastructure backbone: accounts, secure transfer, workflow-job and agent-session scheduling, self-deployed models, sandboxing, storage, MCP registry. | `umrs/` submodule; expanded as "Unified Multimodal Retrieval System" in its README. |
| **Scientific Catalog** | Portable, versioned bundle of a domain's or project's knowledge: materials, RAG/vector index, knowledge graph, skills, MCP definitions, metadata (domain, contributors, version, provenance, licence). | The object exists since 2026-09-22 (`/umrs/scientific-catalogs`, `r4 catalog`); loop wiring and bundle v2 are open (gap D-39, Partial; corrected 2026-10-18 from "not one object yet"). Its contents live in the knowledge spine, the `.kb` bundle, the skills and MCP registries. Do not confuse with the **data catalog** below. |
| **Knowledge graph (KG)** | Structured store of entities, relationships, procedures, results and provenance inside a catalog; grows semi-automatically from the loop and from conversations, by proposal. | Postgres tables `knowledge_nodes` / `knowledge_edges` with `graph_id`, registry `knowledge_graphs` (migration 0069), ontology registry (0091), proposals (0069). |
| **RAG / vector DB** | Retrieval-augmented generation over embedded materials; supplies passages to the model's context. | Postgres + pgvector (`knowledge_node_embeddings`, `memory_vectors`), retrieval facade in `umrs/src/umrs/api/rag_service.py`. |
| **Skill** | Reusable, versioned procedure an agent loads to perform a standard task correctly; part of the catalog. | Agent Skills `SKILL.md` directories (builtin, user, project) + UMRS skill registry. |
| **MCP / custom MCP server** | Model Context Protocol; a custom server wraps a group's dedicated tool so agents can call it. | `umrs/src/umrs/mcp/` registry, `mcp-servers/`, `r4/src/r4/mcp.py`. |
| **Hatch / Hatchling** | Hatch: framework for building and publishing MCP servers (package format `hatch.json` + `server.py`). Hatchling: runtime that discovers, pulls and runs them. | `r4 mcp hatch …`, `r4/src/r4/integrations/hatchling.py`, `mcp-servers/hatch-registry/`. |
| **mini-RiVault** | The one sandbox type: a firewalled Podman container in which UMRS runs every job, agent session and model server. Identifier form: `mini-rivault` (paths, env `RIVAULT_*`). | Compute: podman-compose stack under Slurm (`umrs/src/umrs/mini-rivault-llm/`); remote sessions: rootless Podman on the front-end (`umrs/src/umrs/venues/`). Gap D-12 lists workloads still outside a sandbox. |
| **Sandbox profile** | Named mini-RiVault configuration (gpu-job, cpu-agent, model-server, embedding-job, mcp-trusted) with declared egress allowlist and mounts. | Not yet a contract (gap D-13); today compose profiles + tenant tiers + EgressGuard. |
| **Autonomous research / engineering loop** | R4's plan → execute → observe → update-catalog cycle that pauses for the scientist and ends in a paper or product. | Server-hosted engine `umrs/src/umrs/knowledge/loop.py` as job kind `research_loop_job` (ADR-036); client-side trackers still present (gap D-32). |
| **Multi-agent team-up** | Several role-specialised agents (planner, experimenter, analyst, reviewer) composed by R4 for one task. | Team Engine SPI (ADR-020) is the seam; several older mechanisms coexist (gap D-29). |
| **Context control** | R4's management of what enters the model context: retrieval, history, project state, token budget. | `r4/src/r4/companion/context.py`, `context_budget.py`. |
| **Remote agent session** | An R4 agent session scheduled by UMRS on the front-end (or a CPU-only server), attachable from the console. | "frontend venue", job kind `frontend_session` (ADR-046). |
| **Venue** | Where an agent session or loop runs: `frontend` or `cluster:<name>` today; `cpu:<host>` and trusted host are design targets. | `umrs/src/umrs/venues/`, `research_loop_job.venue`. |
| **Workflow** | A multi-step, schedulable unit of work defined in R4 and executed by UMRS on a cluster. | Workflow schema 2.0.0 (`umrs/src/umrs/workflow/models.py`); `r4 workflow`. |
| **Job / Attempt / Commit pointer** | Job = immutable intent; Attempt = one execution try; commit pointer = which attempt's outputs are current. | `umrs/src/umrs/jobs/` (ADR-002). |
| **Execution authority** | The one scheduler a job is bound to for life: `slurm`, `k8s` or `compose` (single-host Podman/Docker). | ADR-004. |
| **ZeroMQ / CurveZMQ / ZAP** | Messaging library, its Curve25519 encryption, and its authentication protocol; the design's only client–server transport. | `umrs/src/umrs/core/` (`MessageQueue`, `zmq_security`, `session_curve_provider`); today one of two transports (gap D-01). |
| **Gateway (edge)** | Today's authentication and routing edge in front of UMRS: NGINX + auth sidecar, routes `/gw/*`, JWT validation, rate limits, X-Accel downloads. Not a design component; its future is gap D-02. | `gateway/`. |
| **S3 buckets** | Object storage for raw and processed data, addressed by project, written only through UMRS. | SeaweedFS on umrs-front (ADR-055), MinIO elsewhere; per-user `s3cfg` delivery is gap D-16. |
| **Postgres + pgvector** | Relational store and vector index: jobs, projects, knowledge graph, vectors, sessions, trust, grants, audit. | `umrs/src/umrs/migrations/` (0001–0100). |
| **Front-end server** | The cluster's login/gateway host where the UMRS server runs and remote sessions are placed. | `data0` (profile `umrs-front`), RCCS login node (`rccs-cloud`). |
| **Trusted dedicated server** | A host authorised to run MCP servers bound to robots, instruments or restricted systems. | Design target (gap D-49). |
| **Catalog export / bundle** | Self-contained, signed archive of a catalog for import into another installation. | `r4-kb` v1.2 (`umrs/src/umrs/knowledge/bundle_export.py`); the full catalog bundle is gap D-40. |
| **Proposal (knowledge)** | A staged graph-extension the agent asks the scientist to approve; only the decision authorises mutation. | `knowledge_graph_propose` / `knowledge_graph_decide`, migration 0069. |
| **Maturity stage** | Bootstrapped → Learning → Competent → Autonomous: how much the loop asks versus proceeds. | Not modelled yet (gap D-33). |

## Implementation-side terms still in use

| Term | Meaning | Where |
|------|---------|-------|
| **Data catalog** | Per-project index of uploaded objects with summaries, tags and modality (`catalog_objects`); the console `/catalog` asset index reads it. Not the Scientific Catalog. | `umrs/src/umrs/data_catalog/`, `/umrs/catalog`, ADR-011 |
| **Knowledge spine (RC1)** | UMRS-owned knowledge modules: scientific memory, knowledge model, graph API, experiment registry, evidence, search, embeddings. | `umrs/src/umrs/{scientific_memory,knowledge_model,knowledge_graph_api,experiment_registry,evidence,search_api,embeddings_api}` |
| **Companion** | The R4 agent process (harness + session) behind the console and the REPL. | `r4/src/r4/companion/` |
| **Bridge (protocol v1)** | JSONL JSON-RPC between the console and the companion. | `packages/r4-tui-v3/src/bridge/`, `r4/src/r4/companion/ui/` |
| **ACP** | Agent Client Protocol adapter so editors attach `r4 acp`. | `r4/src/r4/acp.py` (ADR-042) |
| **EgressGuard** | Deny-by-default outbound proxy with allowlists, scanning and audit for sandboxes that may talk out. | `umrs/src/umrs/security/`, `mcp-servers/src/r4_mcp/egress_guard.py` (ADR-015, ADR-040) |
| **Tier (system / project / user)** | Ownership and egress class of a provisioned endpoint. | `umrs/src/umrs/tenants/` (experimental), endpoint provisioning |
| **A2L** | Conservation domain for artifact identities, lineage edges, annotations and decisions keyed by job/attempt. | `umrs/src/umrs/a2l/` (ADR-034) |
| **companion_trust** | Server-side per-scope trust allowlist for autopilot. | `umrs/src/umrs/companion_trust/` (ADR-035) |
| **Asset grant** | Version-pinned cross-project read sharing of catalog assets. | `umrs/src/umrs/grants/` (ADR-041) |
| **Inference proxy** | Credential-injecting proxy in the UMRS trust zone for remote sessions. | `umrs/src/umrs/infer_proxy/` (ADR-046) |
| **LiteLLM / vLLM / BentoML** | OpenAI-compatible routing layer, text inference engine, vision serving framework inside a compute mini-RiVault. | `umrs/src/umrs/mini-rivault-llm/`, `umrs/src/umrs/vision_service/` |
| **site.yaml + .env** | Per-deployment site configuration and secrets; nothing is hardcoded. | `umrs/deploy/*/site.yaml`, ADR-008 |
| **genai / rivault-\*** | Legacy deployment and unit names still present in configs. | identifiers only |
| **Wave** | One maintainer-directed agent work unit with a ledger entry. | `docs/development/wave-ledger.md`, CONSTITUTION §9 |
