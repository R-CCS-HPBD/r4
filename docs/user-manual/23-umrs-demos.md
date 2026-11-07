# 23. UMRS Demos

UMRS includes 32 self-contained demos covering every feature. Each demo has a `run_demo.sh` script and a `README.md` with prerequisites and expected output.

All demos work on any deployment (Docker, rccs-cloud, hpbd-atom) as long as the UMRS server is reachable and the client is configured.

## Demo Index

### Foundational (00–10)

| # | Name | Focus |
|---|------|-------|
| 00 | `onboarding` | User enrollment, bucket provisioning, identity confirmation |
| 01 | `health_check` | Server readiness, service availability diagnostics |
| 02 | `text_embedding` | Embed text files into pgvector |
| 03 | `sam3_segmentation` | SAM3 image segmentation via BentoML |
| 04 | `vision_inference` | Generic vision model inference (MAE, ViT) |
| 05 | `rag_search` | Vector search, hybrid BM25+RRF, cross-encoder reranking |
| 06 | `server_maintenance` | Admin tasks (user management, state reset) |
| 07 | `model_registry` | Model registration, listing, staging, downloads |
| 08 | `user_model` | User's own trained model registration & inference |
| 09 | `multistep_vision_pipeline` | Chained vision tasks (detect → segment → classify) |
| 10 | `vision_multi_mode` | Multi-modal vision inference (image + text) |
| 32 | `backbone_durability` | Migrations, factory-resolved PostgreSQL backends, restart recovery proofs (2026-08 durability wave) |

### Advanced (11–24)

| # | Name | Focus |
|---|------|-------|
| 11 | `job_submission` | Durable job lifecycle, retries, status tracking |
| 12 | `pipeline_sdk` | Local Pipeline SDK lifecycle (6 phases) |
| 13 | `plugins` | Plugin spec validation and execution |
| 14 | `video_analysis` | Motion detection on MP4 files |
| 15 | `complex_dag` | Multi-step DAG execution and artifact passing |
| 16 | `custom_workflow` | Server-side workflow upload and execution |
| 17 | `multi_user` | Per-user S3 bucket isolation and role enforcement |
| 18 | `model_lifecycle` | Model versioning, rollout, deprecation |
| 19 | `rag_advanced` | Advanced RAG (hybrid, reranked, semantic caching) |
| 20 | `durable_jobs` | Job deduplication, idempotency, cancellation |
| 21 | `remote_plugins` | External plugin loading over HTTP |
| 22 | `monitor` | TUI job monitor, live updates |
| 23 | `auth_enforcement` | Token generation, role-based access control |
| 24 | `thread_pool` | Query pool metrics, backpressure indicators |

### Enterprise (25–30)

| # | Name | Focus |
|---|------|-------|
| 25 | `endpoint_provisioning` | REST API for Mini-RiVault lifecycle (up, down, list, mounts) |
| 26 | `mcp_servers` | MCP server registration, tool invocation |
| 27 | `pipeline_crud` | Pipeline CRUD, RAG profile composition |
| 28 | `egress_audit` | EgressGuard auditing, network policy enforcement |
| 29 | `llm_completions` | OpenAI-compatible API (chat, text, embeddings) |
| 30 | `file_management` | S3 file upload/download, presigned URLs |

### Cleanup

| # | Name | Focus |
|---|------|-------|
| 99 | `cleanup` | Full demo teardown (buckets, DBs, users, policies) |

## Running Demos

```bash
cd umrs/demos/<demo-number>_<name>
./run_demo.sh
```

### Prerequisites

Most demos require:
1. A running UMRS server
2. Configured `umrs` (connection URL and credentials)
3. Some demos require GPU (vision, embedding, LLM)

### Configuration

```bash
# Set UMRS server URL
umrs config --url http://<umrs-host>:8000

# Or pass per-command
umrs ping --url http://<umrs-host>:8000
```

## Recommended Learning Path

### For End Users

1. `00_onboarding` — Get enrolled
2. `01_health_check` — Verify connectivity
3. `05_rag_search` — Try RAG
4. `07_model_registry` — Explore models
5. `29_llm_completions` — Use LLM API

### For Operators

1. `00_onboarding` — User setup flow
2. `06_server_maintenance` — Admin tasks
3. `11_job_submission` — Job system
4. `17_multi_user` — Multi-tenant setup
5. `25_endpoint_provisioning` — Endpoint management
6. `28_egress_audit` — Security auditing
7. `99_cleanup` — Teardown

### For Developers

1. `12_pipeline_sdk` — Pipeline development
2. `13_plugins` — Plugin system
3. `15_complex_dag` — DAG workflows
4. `16_custom_workflow` — Workflow authoring
5. `26_mcp_servers` — MCP integration

For building custom workflows with Dockerfile-in-workflow, pre-built
images, GPU support, and auto-trigger, see the
[Custom Workflow Guide](../guides/custom-workflows.md).
