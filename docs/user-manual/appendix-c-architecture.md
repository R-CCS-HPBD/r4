# Appendix C — Architecture Overview

## Platform Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                             Client Layer                                  │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────┐│
│  │   n8n   │  │ Jupyter │  │   r4    │  │   r4    │  │   MCP Servers   ││
│  │Workflows│  │Notebooks│  │   CLI   │  │   SDK   │  │                 ││
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  └────────┬────────┘│
│       └────────────┴────────────┴────────────┴────────────────┘          │
└────────────────────────────────────┬──────────────────────────────────────┘
                                     │
                       ┌─────────────▼─────────────┐
                       │     Platform Gateway      │
                       │  • Auth & Project Scoping │
                       │  • Rate Limiting          │
                       │  • Audit Logging          │
                       │  /gw/llm/* /gw/rag/*      │
                       └─────────────┬─────────────┘
                                     │
                 ┌───────────────────┼───────────────────┐
                 │                   │                   │
                 ▼                   ▼                   ▼
       ┌─────────────────┐  ┌───────────────────────────────────────────┐
       │      UMRS       │  │        Mini-RiVault (per endpoint)        │
       │ • File Handling │  │  ┌──────────┐  ┌──────────┐              │
       │ • S3/MinIO      │  │  │  LiteLLM │  │   vLLM   │              │
       │ • Embeddings    │  │  │  :11434  │  │  :8000   │              │
       │ • RAG Pipeline  │  │  └──────────┘  └──────────┘              │
       │ • Orchestrator  │  │  ┌──────────┐  ┌──────────────────────┐  │
       │ • Durable Jobs  │  │  │   RAG    │  │   MCP Servers       │  │
       │ • Model Registry│  │  │  Index   │  │   (user-mounted)    │  │
       │ • Workflows     │  │  └──────────┘  └──────────────────────┘  │
       │ • Context Store │  │  ┌──────────────────────────────────┐    │
       └─────────────────┘  │  │           EgressGuard            │    │
                            │  │  (content scanner + allowlist)   │    │
                            │  └──────────────────────────────────┘    │
                            │  Pre-staged Models (read-only)           │
                            └──────────────────────────────────────────┘
```

## Component Boundaries

### What Each Component Owns

| Component | Responsibilities |
|-----------|-----------------|
| **r4 (SDK/CLI)** | User experience, authentication, request wrappers |
| **r4-contracts** | Shared DTOs, schemas, local validators |
| **Gateway** | Auth, routing, rate limiting, audit, file fetch |
| **UMRS** | Jobs, files, endpoints, workflows, RAG, models, context |
| **Mini-RiVault** | Isolated inference, RAG execution, MCP tools |

### Import Boundaries

```
r4 ──depends on──▶ r4-contracts
         │
         ├──────────▶ Gateway (HTTP)
         │
         └──────────▶ UMRS (HTTP/ZMQ)

r4 NEVER imports umrs
r4 NEVER imports gateway
gateway NEVER imports umrs (proxy only)
```

## Data Flow

### Chat Request

```
User → r4 CLI → Gateway → UMRS → Mini-RiVault → LiteLLM → vLLM → Response
```

### File Upload

```
User → r4 CLI → UMRS S3 Upload → Presigned URL → vLLM/LiteLLM
```

### RAG Query

```
User → r4 CLI → Gateway → UMRS → Mini-RiVault RAG → pgvector → LLM → Response
```

## Hybrid Orchestration

Separate resource allocation from container launch:

| Concept | Purpose | Implementations |
|---------|---------|----------------|
| **ExecutionTarget** | Allocates resources | SlurmTarget, K8sTarget, ComposeTarget |
| **RuntimeWrapper** | Launches container | SingularityWrapper, DockerInJobWrapper, K8sJobWrapper |

| Deployment | ExecutionTarget | RuntimeWrapper |
|------------|----------------|----------------|
| Slurm HPC | SlurmTarget | SingularityWrapper |
| Kubernetes | K8sTarget | K8sJobWrapper |
| Development | ComposeTarget | DockerWrapper |

## Gateway Routes

```
/gw/llm/*   → UMRS → Mini-RiVault LiteLLM → vLLM
/gw/rag/*   → UMRS RAG service
/gw/mcp/*   → UMRS → Mini-RiVault MCP servers
```

## Security Boundaries

```
User Request
     │
     ▼
┌─────────────────┐
│  Gateway        │  ← Auth, rate limit, routing, file fetch
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  UMRS           │  ← Job orchestration, durable tracking
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────┐
│  Mini-RiVault (Security Boundary)                   │
│  ┌─────────┬─────────┬─────────┬─────────────────┐  │
│  │  vLLM   │ LiteLLM │   RAG   │  MCP Servers    │  │
│  └─────────┴─────────┴─────────┴─────────────────┘  │
│                       │                             │
│              ┌────────▼────────┐                    │
│              │  EgressGuard    │                    │
│              └────────┬────────┘                    │
└───────────────────────┼─────────────────────────────┘
                        │ (if allowed)
               [Allowlisted External]
```
