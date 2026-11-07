# n8n Integration for R4 Platform

**Status (2026-09-15): maintained as an OPTIONAL integration.** The integration
runs against the R4 platform's external REST surface and is NOT part of the
core deploy graph — the platform runs without it. The pinned container is
`n8nio/n8n:latest` (floating tag; treat upgrades as deliberate hops) and the
sample workflows under `workflows/` are maintained but not CI-gated. Bring it
up only for automation-heavy sessions.

This directory contains configuration and deployment files for n8n workflow automation.

## Overview

n8n is a workflow automation tool that connects with the R4 Platform
to create automated pipelines involving:

- LLM chat and completion
- RAG document processing
- MCP tool invocation
- Data transformation

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         n8n Workflows                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  LLM Chat    │  │  RAG Query   │  │  Data Pipeline       │  │
│  │  Workflow    │  │  Workflow    │  │  Workflow            │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
└─────────┼─────────────────┼─────────────────────┼───────────────┘
          │                 │                     │
          ▼                 ▼                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Platform Gateway                             │
│                                                                  │
│   /gw/llm/*        /gw/rag/*        /gw/mcp/*                  │
└─────────────────────────────────────────────────────────────────┘
```

## Important Rules

1. **n8n NEVER talks to vLLM/LiteLLM directly**
   - All requests go through Platform Gateway
   - Routes: `/gw/llm/*`, `/gw/rag/*`, `/gw/mcp/*`

2. **File uploads go through UMRS**
   - Upload to Gateway → UMRS → S3
   - Get presigned URL for LLM access
   - Never direct file transfer to LLM
   - **Prerequisite (bug.memo N8N2, 2026-04-25):** before a workflow can
     reference a file, it must already exist in the UMRS-backed object
     store. Either (a) upload it once via `r4 rag upload …` / the Gateway
     `/gw/rag/upload` route and pass the returned `document_id` into the
     workflow, or (b) stage it under the UMRS shared mount out-of-band
     and reference it by path. The n8n nodes do NOT upload raw files.

3. **Authentication via Gateway credentials**
   - Configure API key in n8n HTTP Request node
   - Gateway handles JWT validation

## Deployment

### Docker Compose

```bash
# Start n8n with platform network
docker-compose -f docker-compose.n8n.yaml up -d
```

### Kubernetes

```bash
# Deploy to K8s cluster
kubectl apply -f k8s/
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `N8N_BASIC_AUTH_USER` | n8n admin username | `admin` |
| `N8N_BASIC_AUTH_PASSWORD` | n8n admin password | Required |
| `GATEWAY_URL` | Platform Gateway URL | `http://gateway:8000` |
| `GATEWAY_API_KEY` | API key for Gateway | Required |
| `N8N_ENCRYPTION_KEY` | Encryption key for credentials | Required |

### Gateway Credentials Template

See [credentials/](./credentials/) for n8n credential templates.

### Gateway URL: env vs. credential (bug.memo N8N1, 2026-04-25)

The bundled workflow JSON under [workflows/](./workflows/) currently
references the gateway via `{{ $env.GATEWAY_URL }}` so that it can be
imported without any configuration — as long as the container has
`GATEWAY_URL` set (docker-compose passes it through automatically; K8s
uses the ConfigMap).

The custom RiVault* nodes (under `nodes/`) instead read from the
`RiVaultApi` credential (`credentials.gatewayUrl`). When you are
mixing stock HTTP Request nodes and RiVault* nodes in the same workflow
**the two must point at the same gateway**. Keep them in sync by:

1. Setting `GATEWAY_URL` in `env.n8n` / the ConfigMap, and
2. Configuring the `RiVaultApi` credential's `Gateway URL` field to the
   same value when you first log into the n8n UI.

If you fork a workflow for per-environment use, prefer the credential
path (drop in a RiVault* node or wire the credential into the HTTP
Request node's Authentication) so the URL is captured in one place.

## Workflow Templates

Pre-built workflow templates are available in [workflows/](./workflows/):

- `llm-chat.json` - Basic LLM chat workflow
- `rag-query.json` - RAG document query workflow
- `data-pipeline.json` - Data processing pipeline

## Files

```
n8n/
├── README.md                    # This file
├── docker-compose.n8n.yaml      # Docker Compose deployment
├── env.n8n.template             # Environment template
├── k8s/                         # Kubernetes manifests
│   ├── deployment.yaml
│   ├── service.yaml
│   └── configmap.yaml
├── credentials/                 # Credential templates
│   └── gateway-credential.json
└── workflows/                   # Workflow templates
    ├── llm-chat.json
    └── rag-query.json
```

## Security

- n8n runs in isolated container/pod
- Credentials stored encrypted
- Network access limited to Gateway only
- No direct access to internal services
