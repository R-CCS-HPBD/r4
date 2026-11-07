# 21. UMRS Operations

UMRS (Unified Model Runtime Service) is the data plane and orchestration backend. This chapter covers operator-facing management tasks.

## UMRS Client

The `umrs` CLI provides direct diagnostics and operations:

```bash
umrs --help
```

### Connectivity

```bash
# Check server readiness
umrs ping --url <umrs-url>

# Server status
umrs status all
```

### File Operations

```bash
umrs data upload <file> --url <umrs-url>
umrs data dir-upload <dir> --url <umrs-url>
umrs data download --url <umrs-url>
umrs bucket list --url <umrs-url>
```

### Model Registry

```bash
umrs model list --url <umrs-url>
umrs model register <name> --path <path> --url <umrs-url>
umrs model download <name> --url <umrs-url>
```

### Job Operations

```bash
umrs job submit <type> --url <umrs-url>
umrs job status <job-id> --url <umrs-url>
umrs job list --url <umrs-url>
umrs job cancel <job-id> --url <umrs-url>
```

### Workflow Operations

```bash
umrs upload-workflow <file> --url <umrs-url>
umrs run-workflow <name> --url <umrs-url>
umrs workflow list --url <umrs-url>
```

## Monitoring

### TUI Monitor

Live multi-tab dashboard:

```bash
umrs monitor --url <umrs-url>
```

Tabs: Jobs | Status | Models | Workflows | Connection

### Server Maintenance

```bash
umrs check --url <umrs-url> --admin-token <token>
umrs recompute --url <umrs-url> --admin-token <token>
umrs reload --url <umrs-url> --admin-token <token>
```

## REST API Endpoints

UMRS exposes a FastAPI surface for platform integration:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/umrs/endpoints/provision` | POST | Provision Mini-RiVault endpoint |
| `/umrs/endpoints/{id}` | GET/DELETE | Inspect/teardown endpoint |
| `/umrs/mcp/servers` | GET/POST | MCP server management |
| `/umrs/mcp/invoke` | POST | Invoke MCP tool |
| `/umrs/components/{chunkers,embedders,rerankers}` | GET | RAG component registry (the `/umrs/pipelines` CRUD was deleted 2026-10-18) |
| `/umrs/llm/v1/models` | GET | List available LLM models |
| `/umrs/llm/v1/chat/completions` | POST | Chat completion |
| `/umrs/llm/v1/embeddings` | POST | Text embeddings |
| `/umrs/files/` | GET/POST/DELETE | File management |
| `/umrs/egress_audit/audit` | GET | Egress audit logs |

## Durable Job System

### Job States

```
queued → running → completed
                 → failed (→ retry → running)
                 → cancelled
                 → lost (crash recovery)
```

### Crash Recovery

On UMRS restart:
1. All in-flight attempts are marked `LOST`
2. Parent jobs are requeued (if retries remain)
3. New attempts created from scratch
4. No backend cleanup needed — relies on isolation

### Job/Attempt Separation

- **Job** = user intent (immutable spec)
- **Attempt** = one execution try (multiple per job on retries)
- **Commit Pointer** = marks which attempt's outputs are current
- All outputs scoped to `s3://…/jobs/<job_id>/attempt_<n>/…`

## Orchestration Backends

| Backend | Target | Wrapper | Environment |
|---------|--------|---------|-------------|
| `slurm` | SlurmTarget | SingularityWrapper | HPC |
| `k8s` | K8sTarget | K8sJobWrapper | Kubernetes |
| `compose` | ComposeTarget | DockerWrapper | Development |

## Deployment Validation

### Site profile

```bash
cd umrs/deploy/<site-profile>
set -o allexport && source .env && set +o allexport
export UMRS_SITE_CONFIG="$PWD/site.yaml"

# Run validation
./validate_harness.sh --case text

# Manual LiteLLM check
curl -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://<compute-node>:8001/v1/models
```

### Quick Redeploy

```bash
cd /path/to/umrs/deploy/<site-profile>
git pull
pip install -e ../../src
./stop.sh && ./start.sh
```

## Context Storage

UMRS stores conversations and memory durably:

| Table | Purpose |
|-------|---------|
| `conversations` | Thread of messages with cache-invalidation `rev` |
| `messages` | Single turn: role, content, token_count, seq |
| `conversation_summaries` | Compacted older messages |
| `memory_items` + `memory_vectors` | Long-term retrievable facts (pgvector) |

## Security Invariants

| Invariant | Rule |
|-----------|------|
| Model download egress | Only jobs with public egress; inference is offline |
| Execution authority | Never changes after job creation |
| Gateway file fetch | Centralized auditing, size enforcement, PII control |
| RAG inside mini-RiVault | All indexing/retrieval inside containers |
| MCP inside mini-RiVault | No standalone external MCP servers |

## Durability & Migrations (2026-08 wave)

All research-layer state, workflow executions, and connector sync state are
**Postgres-backed** when the environment provides a database. Semantics are
env-driven per store (`pg|memory|auto`, default `auto` — PG when
`DB_HOST`/`DATABASE_URL` present, else in-memory). See
[Appendix B — UMRS Store Backends](appendix-b-env-vars.md#umrs-store-backends-durability-2026-08)
for the full env-var list and
[docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)
for per-module connectivity.

**Apply migrations on a new host:**

```bash
# From a UMRS container on the cluster (python has psycopg2 inside):
python -m umrs.migrations --db-url postgresql://user:pass@host:5432/umrs_db

# Or status check only:
python -m umrs.migrations --db-url .../umrs_db --status
```

**Rebuild + redeploy after a backbone change:** redeploy the UMRS servers
with your site's deployment tooling (for a single host,
[deploy/standalone](../../deploy/standalone/README.md)), then apply pending
migrations as shown above and restart the servers once if any were applied.
Treat host checkouts as read-only and let the tooling own them. The R-CCS
sites are deployed by the development repository's converge workflow; its
runbook is in
.github/instructions/deployment-operations.instructions.md,
which is not part of the public tree.

**Canonical identity backfill (one-off per installation, 2026-09-24):**
artifacts ingested before 2026-09-24 carry no
`metadata.canonical_identity`, so a bundle import cannot recognise them as
the same document. New ingests stamp themselves. Run the backfill once after
the deploy that ships it, dry-run first. Servers that share one database and
one bucket need a single run from any one server container (an operator
step, as the deploy user):

```bash
podman exec <umrs-server-container> python -m umrs.knowledge.identity_backfill          # dry-run: counts only
podman exec <umrs-server-container> python -m umrs.knowledge.identity_backfill --apply  # write
# optional: --project-id <project> to limit the run
```

The output is one JSON line (`scanned`, `stamped`, `unreadable`,
`no_object`, `by_normalization`). `no_object` counts rows minted without
bytes (legacy `rag://` corpus rows, loop findings); they are skipped
without a fetch. The run pages by artifact id and never overwrites an
existing stamp, so re-running is safe. Rows whose bytes are unreadable, or
over 64 MiB, stay unstamped on purpose; export computes their identity on
the fly.

**Canonical durable stores now live:**

| Module | Tables |
|--------|--------|
| scientific_memory | `scientific_memory_entries` |
| evidence | `evidence_items` |
| experiment_registry | `experiments`, `experiment_results` |
| embeddings_api | `sat_embeddings` (pgvector) |
| knowledge_graph_api | `sat_kg_nodes`, `sat_kg_edges` |
| knowledge_model | `sat_knowledge_objects` (+versions+tombstones) |
| tenants | `sat_tenants` |
| hypothesis_lifecycle | `hypotheses`, `hypothesis_history` |
| knowledge core | `knowledge_artifacts` (a2l + store), `knowledge_ir`, `knowledge_nodes`, `knowledge_edges`, `readiness_approvals` |
| workflow executions | `workflow_executions` |
| connector sync | `connector_sync_state`, `connector_seen_hashes` |

Restart/failover recovery: each factory warms local cache from Postgres on
first read (read-through); no behavioral warm-up step needed from
operators.

## Next Steps

- [Chapter 22 — Demo Suite](22-demos.md)
- [Appendix B — Environment Variables](appendix-b-env-vars.md)
