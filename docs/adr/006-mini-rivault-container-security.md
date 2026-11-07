# ADR-006: Offline Inference Container Security Model

**Status:** Accepted
**Date:** 2026-05-13
**Relates to:** INV-05 (offline inference), INV-09 (RAG/MCP inside mini-RiVault), INV-11 (Gateway file fetch), INV-12 (static model names)

## Context

Mini-RiVault endpoints run vLLM + LiteLLM inside containers (Singularity on HPC,
Podman in development). These containers process user prompts and generate LLM
outputs. They have access to GPU resources and model weights.

A contributor might add network access to inference containers for convenience
(e.g., downloading models on demand, calling external APIs from MCP tools).

## Decision

Inference containers run **fully offline**:

```bash
TRANSFORMERS_OFFLINE=1
HF_HUB_OFFLINE=1
```

Model weights are pre-staged on the filesystem. Model downloads are a separate
job type with explicit egress permission.

### Security Boundaries

| Component | Network Access | Reason |
|-----------|---------------|--------|
| Inference container (vLLM) | None | Cannot download arbitrary code |
| LiteLLM proxy | Internal only | Talks to vLLM, nothing else |
| Model download job | Controlled egress | Only job type with internet access |
| MCP servers (inside mini-RiVault) | Via EgressGuard | Audited, domain-allowlisted |
| Gateway file fetch | Presigned URLs only | Centralized size/PII enforcement |

### File Access Pattern

Files are not uploaded to vLLM directly. Instead:
1. UMRS uploads file to S3/MinIO
2. Gateway generates presigned URL (TTL-limited)
3. Gateway fetches file content on behalf of the inference request
4. Inference container never accesses URLs directly

## Consequences

- `serve-model.slurm` sets `TRANSFORMERS_OFFLINE=1` and `HF_HUB_OFFLINE=1`
- Model download jobs use `EgressGuard` for controlled internet access
- Gateway performs all file fetches centrally (auditing, size limits, PII scanning)
- Inference containers that attempt network access fail immediately
- Model names in LiteLLM config are static (`rivault-gpt-oss-static`)
- Dynamic model naming is not supported

## Alternatives Considered

- **Network-enabled inference**: Allows model auto-download but creates a massive
  attack surface. An adversarial prompt could trigger arbitrary downloads.
- **Per-request file uploads to vLLM**: Would require vLLM to handle file parsing,
  bypassing Gateway's security scanning.

## Amendment: Mini-RiVault as Security Pattern (2026-07-05)

**Relates to:** ADR-013 (Knowledge Portability and Compute/Knowledge Separation)

### Redefinition

Mini-RiVault is a **security pattern**, not a compute pattern. The security
pattern consists of:

1. **Container isolation** — untrusted workloads run inside containers
2. **Egress control** — no outbound network except through EgressGuard
3. **Read-only mounts** — model weights and project data are read-only
4. **Offline inference** — `TRANSFORMERS_OFFLINE=1`, `HF_HUB_OFFLINE=1`

This pattern is applicable to **any untrusted workload**, not just GPU inference:

| Workload | GPU Required? | Uses Mini-RiVault Pattern? |
|----------|---------------|---------------------------|
| vLLM inference | ✅ | ✅ (compute node, Slurm-allocated) |
| RAG retrieval | ❌ | ✅ (compute node OR frontend server) |
| MCP tool execution | ❌ | ✅ (compute node OR frontend server) |
| Knowledge indexing | ❌ | ✅ (compute node OR frontend server) |
| Model download | ❌ | ✅ (controlled egress job) |

### Deployment Flexibility

RAG/MCP services can be deployed in two modes:

**Mode 1: Co-located (current behavior)**
- RAG query engine runs inside mini-RiVault container on compute node
- Advantage: co-located with vLLM, no network hop for retrieval
- Disadvantage: requires GPU allocation, tied to endpoint lifecycle

**Mode 2: Frontend-deployed (new behavior)**
- RAG query engine runs as persistent service on frontend server (CPU-only)
- Advantage: no GPU needed, persists across endpoint lifecycles, serves multiple endpoints
- Disadvantage: network hop from compute node to frontend for retrieval

### INV-09 Relaxation

**Previous INV-09:** "RAG and MCP must run inside mini-RiVault."

**New INV-09:** "RAG and MCP run inside sandboxed containers (mini-RiVault
pattern). The sandbox may be co-located with inference on compute nodes OR a
standalone sandboxed service on the frontend/login node. The key invariant is
container isolation + egress control, NOT co-location with GPU inference."

### Knowledge Portability

Knowledge (documents + embeddings + graph + catalog) is exportable as a
portable bundle (`.kb` format). See ADR-013 for the bundle format specification.

**Export:** `r4 knowledge export <project> --output my-project.kb`
**Import:** `r4 knowledge import <bundle>`
**Use with inference:** `r4 up --knowledge <bundle>`
