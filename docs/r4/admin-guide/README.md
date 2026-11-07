# Administrator Guide

This guide covers deployment, configuration, and administration of the R4 Platform for system administrators and service operators.

End users should use the [Basic User Quick Start](../quickstart.md) and [R4 documentation index](../index.md). This repo does not ship a separate user-guide tree beyond those user-facing docs.

## Contents

1. [Prerequisites](#prerequisites)
2. [Deployment](#deployment)
3. [Admin CLI Tools](#admin-cli-tools)
4. [User Management](#user-management)
5. [Model Management (System Pool)](#model-management-system-pool)
6. [Configuration](#configuration)
7. [Hybrid Deployment Modes](#hybrid-deployment-modes)
8. [Security](#security)
9. [Monitoring & Operations](#monitoring--operations)
10. [Troubleshooting](#troubleshooting)
11. [Maintenance Procedures](#maintenance-procedures)

## Detailed Deployment Guides

| Guide | Description |
|-------|-------------|
| [HPC Multi-Node Deployment](hpc-deployment.md) | Ansible-based deployment for HPC clusters with Slurm and Singularity |
| [Docker Deployment Guide](docker-deployment.md) | Docker / Podman Compose deployment for development and testing |
| [Kubernetes Deployment Guide](kubernetes-deployment.md) | Production Kubernetes deployment with Helm and Kustomize |
| Server/Client Deployment | Shared server deployment and thin client onboarding split |
| [Library Group Structure](../../architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md) | Package ownership, import rules, and server/client install sets |

## Site Profiles

UMRS ships named site profiles for scheduler, runtime, Slurm module, and log-path defaults. Select one profile per deployment with `UMRS_SITE_PROFILE` or `RIVAULT_SITE_PROFILE`; Ansible deployments should set the `umrs_site_profile` variable, which renders into the UMRS service environment and shared `rivault-env.sh`.

| Profile | Intended deployment | Main defaults |
|---------|---------------------|---------------|
| `rccs-cloud` | RCCS shared login node plus Slurm compute partitions | Slurm + rootless Podman / podman-compose runtime defaults |
| `lab-podman` | Single lab server or small shared host | Local scheduler assumptions with Podman runtime |
| `compose-dev` | Developer compose/E2E harness | Compose-style local runtime defaults |
| `k8s-cluster` | Kubernetes cluster deployment | Kubernetes scheduler/runtime defaults |

The generic Ansible inventory leaves `umrs_site_profile` empty so existing deployments keep their explicit variables. Site profiles are not secret stores; keep passwords, JWT keys, LiteLLM keys, and admin tokens in Ansible Vault or deployment-specific secret managers.

The `rccs-cloud` defaults assume a site-owned Slurm/Podman harness of the shape shown in `umrs/deploy/templates/login-node/`. Some of the generic HPC deployment pages below still describe Singularity/Apptainer.

## Package And Library Layout

Operators deploy a group of packages rather than a single monolith:

| Package | Server Deployment Role |
|---|---|
| `r4-contracts` | Shared DTOs and validators used by `r4` and platform tooling |
| `r4` | User CLI/SDK and operator smoke-test client |
| `umrs` | Durable server, admin CLI, jobs, endpoints, models, files, workflows, and RAG |
| `r4-gateway` / `gateway/` | Gateway edge and auth sidecar |
| `r4-mcp` / `mcp-servers/` | Platform-managed MCP servers |

For editable deployments, refresh the Python packages every deploy:

```bash
python -m pip install -e ./r4-contracts -e ./r4 -e ./umrs/src
```

`r4` is installed in service environments for validation convenience. UMRS must still be independently workable without importing `r4`.

---

## Prerequisites

### Software Requirements

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.10+ | Platform services |
| PostgreSQL | 14+ | UMRS database, pgvector |
| MinIO/S3 | Latest | Object storage |
| Podman/Docker | 4.0+ / 20.10+ | Container runtime (login nodes) |
| Singularity | 3.8+ | Container runtime (HPC compute nodes) |
| CUDA | 11.8+ | GPU support |
| Ansible | 2.15+ | Standard deployment tool |

### Orchestrator Requirements

Choose ONE of the following:

| Orchestrator | Use Case | Requirements |
|--------------|----------|--------------|
| **Slurm** | HPC clusters | Slurm 20.02+, sbatch, squeue, scancel |
| **Kubernetes** | Cloud/server | K8s 1.25+, GPU Operator |
| **Podman Compose / Docker Compose** | Development | Podman 4.0+ or Docker Compose v2+ |

---

## Deployment

### Standard Path: Your Site Tooling

The R-CCS sites are deployed with Ansible (`deploy/ansible`); that deployment
is kept in the development repository and is not part of the public tree
(2026-11-07). With the public repository:

- one host: [deploy/standalone](../../../deploy/standalone/README.md);
- an HPC login node: the [HPC Quick Start](hpc-quickstart.md), built on the
  UMRS template `umrs/deploy/templates/login-node/`;
- one front-end serving several clusters: the UMRS template
  `umrs/deploy/templates/frontend-multi-hpc/`.

Drive these with your own configuration management for managed hosts.
[HPC Multi-Node Deployment](hpc-deployment.md) describes the shape of a
multi-node Ansible deployment, including inventory setup, secrets management,
and multi-node scenarios.

### Alternative: Docker / Podman Compose (Development)

For local development and E2E testing only:

```bash
docker compose -f docker-compose.e2e.yaml up -d
r4 health          # preferred
curl http://localhost:8080/health   # direct HTTP probe
```

See [Docker Deployment Guide](docker-deployment.md).

### Alternative: Kubernetes

For cloud/server production environments:

```bash
kubectl apply -k gateway/k8s/
# or
helm install rivault helm/rivault/ -f values.yaml
```

See [Kubernetes Deployment Guide](kubernetes-deployment.md).

### Post-Deployment Checklist

After deploying via any path:

```bash
# 1. Verify services are running
r4 health
r4 health full
umrs ping

# 2. Bootstrap admin user and token (first time only)
umrs-server admin bootstrap --admin-user admin --json

# 3. Seed the system model pool (see Model Management below)
r4 model stage Qwen/Qwen2.5-7B-Instruct --name qwen2-7b --scope system

# 4. Bring up a system endpoint
r4 endpoint up --model qwen2-7b --tier system --wait
```

---

## Admin CLI Tools

Three CLI tools are available. `r4` is the end-user CLI for Gateway-mediated workflows. `umrs` is an operator/developer diagnostic client for direct UMRS transport checks and migration helpers; its legacy `admin` command is permanently disabled. `umrs-server admin` is the only supported admin CLI for user, token, and Slurm enrollment operations.

### Install

```bash
# User CLI and UMRS diagnostic client
pip install -e ./r4
pip install -e ./umrs/src

# umrs-server is included in the same package as umrs
umrs-server admin --help
```

### `umrs-server admin` — Admin-Only Operations

The `umrs-server admin` CLI manages user records, admin tokens, and Slurm account mapping directly on the server. It manipulates `user_files/*.json` and `admin_tokens.json` without requiring a running daemon.

#### User Management

```bash
# Add a new user
umrs-server admin user add alice \
    --fullname "Alice Smith" \
    --email alice@example.com \
    --role user \
    --slurm-account research-gpu \
    --project team-alpha

# List users
umrs-server admin user list
umrs-server admin user list --role admin --json

# Show a specific user
umrs-server admin user show alice

# Modify user
umrs-server admin user set-role alice admin
umrs-server admin user set-slurm-account alice hpc-premium
umrs-server admin user set-slurm-account alice hpc-premium --site cluster-a  # per-site
umrs-server admin user add-to-project alice team-beta
umrs-server admin user remove-from-project alice team-alpha

# Disable / enable
umrs-server admin user disable alice
umrs-server admin user enable alice

# Delete (soft-delete by default, --purge to remove file)
umrs-server admin user delete alice
umrs-server admin user delete alice --purge -y
```

Mutating user operations automatically send `SIGHUP` to the running daemon so it hot-reloads user records. Use `--no-reload` to suppress this.

#### Admin Tokens

```bash
# Generate an admin token (plaintext shown ONCE)
umrs-server admin token generate my-admin --description "admin token"

# List existing tokens (metadata only)
umrs-server admin token list

# Revoke
umrs-server admin token revoke my-admin -y

# Generate a Gateway JWT (dev/testing)
umrs-server admin token generate-jwt --user alice --project team-alpha --days 90

# Generate JWT with JSON output (includes claims + expiry)
umrs-server admin token generate-jwt --json

# Use with r4 auth login (saves profile + authenticates in one step)
r4 auth login --gateway-url http://localhost:8080 \
    --api-key "$(umrs-server admin token generate-jwt --user alice --project team-alpha)" \
    --project team-alpha
```

> **Note:** `generate-jwt` signs tokens with `$JWT_SECRET` (or `--secret`).  
> The default dev secret is used when neither is set.  
> These locally generated JWTs are project-scoped because the current Gateway
> authorization model reads `project_id` from the token claims. They are useful
> for dev/testing and for narrowly scoped automation. In production with Okta,
> JWTs come from the identity provider instead.

#### Bootstrap (First-Run)

Idempotent first-run setup that creates the admin user and an initial token:

```bash
umrs-server admin bootstrap --admin-user admin --json
```

#### Slurm Helpers

```bash
# List Slurm accounts (read-only, requires sacctmgr on PATH)
umrs-server admin slurm accounts
```

#### Daemon Reload

```bash
# Explicitly reload user records without restarting the daemon
umrs-server admin reload
```

### `umrs` — Operator Commands

The `umrs` CLI provides operator-level commands for health checks and model registration.

> **Note:** `umrs` is a **network client** — it requires the UMRS server to be running and reachable. On HPC clusters, run these commands from **login nodes** (not compute nodes). This is different from `umrs-server admin`, which operates directly on local files without requiring a running daemon.

```bash
# Health / status
umrs ping
umrs status all

# Model registration (alternative to r4 model register)
umrs model register qwen2-7b /shared/models/qwen2-7b \
    --type llm --scope system --description "Shared system model"

umrs model list --scope system
umrs model download intfloat/e5-base-v2 --scope user --server-side
```

### Shared CLI Commands

The following `r4` commands are available to **all users** (not admin-only). They are documented in the [Quick Start](../quickstart.md), [R4 docs index](../index.md), and [CLI Reference](../reference/cli.md):

- `r4 health`, `r4 health full`, `r4 health account` — health checks and credential diagnostics
- `r4 model list/info/stage/register/publish` — model management
- `r4 project list/create/switch/current/delete` — project management
- `r4 job list/describe/cancel/retry` — job tracking
- `r4 endpoint list/up/down/status/logs` — endpoint management
- `r4 chat`, `r4 rag`, `r4 mcp` — inference and tools

---

## User Management

### Authentication

The platform supports two authentication methods:

**OIDC (preferred for shared deployments):**
```bash
# Users authenticate via OIDC browser login (Authorization Code + PKCE).
# This is NOT the Device Authorization Grant — the CLI opens the browser directly.
# Requires: Okta Native Application (public client, no client_secret).
# See: deploy/oauth2-proxy/README.md Step 5 for Okta setup.
r4 auth login --oidc
```

Target model for shared deployments:

- one user login session authenticated by the identity provider
- active project selected from CLI context or request parameters
- Gateway and UMRS enforce project membership server-side for each request

**JWT Bearer Tokens:**

Gateway expects bearer JWTs and extracts tenant information from token claims.
In the current dev/testing flow, these JWTs are project-scoped.

- Required claim: `project_id` (also accepts `project`, `tenant_id`, or `tenant`)
- User identity: `sub` or `user_id`
- Optional: `email`, `roles`

```bash
# Gateway JWT validation config
JWT_ISSUER=https://auth.example.com
JWT_AUDIENCE=rivault-api
```

The Gateway does not initiate an OAuth or OIDC flow itself. It validates JWTs that were issued elsewhere and presented in the `Authorization: Bearer ...` header.

For human users who switch projects often, the preferred long-term model is OIDC
login plus server-side project authorization checks, not one manually generated
JWT per project. Keep `generate-jwt` as a current dev/testing tool and as a way
to mint deliberately narrow automation credentials.

### User Lifecycle

```bash
# Create user with project and Slurm account assignment
umrs-server admin user add alice \
    --role user \
    --slurm-account research-gpu \
    --project team-alpha

# Per-site Slurm account (multi-cluster deployments)
umrs-server admin user add alice \
    --role user \
    --slurm-account research-gpu --site cluster-a \
    --project team-alpha

# Promote to admin
umrs-server admin user set-role alice admin

# Offboard (soft-delete preserves job/audit history)
umrs-server admin user disable alice

# Hard delete when needed
umrs-server admin user delete alice --purge -y
```

### Roles

| Role | Capabilities |
|------|-------------|
| `user` | Inference, model staging (user/project scope), job submission |
| `admin` | All of user + system model management, policy management, user admin |

---

## Model Management (System Pool)

The admin only owns the shared **system pool** and policy/quota controls. Project and user models are self-service: users run `r4 model stage/register` themselves. See the [Quick Start](../quickstart.md) and [CLI Reference](../reference/cli.md) for the user-facing path.

### Admin vs User Boundary

| Task | `--scope system` | `--scope project` | `--scope user` |
|------|------------------|-------------------|----------------|
| Stage from HuggingFace | Admin only | Project member | Self-service |
| Register existing path | Admin only | Project member | Self-service |
| Delete | Admin only | Project admin / owner | Owner |
| Set policies, quotas, warm pools | Admin only | Admin only | Admin only |

Key invariant: **models are never downloaded inside an inference container**. All downloads run as UMRS durable jobs outside of mini-RiVault (`TRANSFORMERS_OFFLINE=1`).

### Seeding the System Pool

```bash
# Via r4 CLI
r4 model stage Qwen/Qwen2.5-7B-Instruct \
    --name qwen2-7b --scope system --type llm

r4 model register qwen2-7b \
    --path /shared/models/qwen2-7b \
    --scope system --type llm \
    --hf-id Qwen/Qwen2.5-7B-Instruct

# Or via umrs
umrs model register qwen2-7b /shared/models/qwen2-7b \
    --type llm --scope system --description "Shared system model"
```

### Offline Fallback

For air-gapped sites where `r4 model stage` cannot reach HuggingFace:

```bash
# On a host with internet
huggingface-cli download Qwen/Qwen2.5-7B-Instruct --local-dir /tmp/qwen2-7b
rsync -av /tmp/qwen2-7b user@hpc:/shared/models/qwen2-7b

# Then register
r4 model register qwen2-7b --path /shared/models/qwen2-7b --scope system --type llm
# or: umrs model register qwen2-7b /shared/models/qwen2-7b --type llm --scope system
```

### Model Policies (admin only)

Policies enforce per-model project scoping, concurrency, daily token quotas, scheduling priority, and warm-pool behavior. No stable `r4 policy` CLI exists today; manage via the UMRS REST API.

| Endpoint | Purpose |
|----------|---------|
| `GET    /umrs/models/policies` | List existing policies |
| `POST   /umrs/models/policies` | Create a policy for a model |
| `PATCH  /umrs/models/policies/{policy_id}` | Update fields |
| `DELETE /umrs/models/policies/{policy_id}` | Remove a policy |

Key fields: `model_name`, `allowed_projects`/`denied_projects`, `max_concurrent_endpoints`, `max_tokens_per_day`, `priority`, `enable_warm_pool`, `warm_pool_min/max/ttl_seconds`, `is_system_model`, `default_serving_profile`.

Example:

```bash
curl -X POST https://<gateway-host>/gw/umrs/models/policies \
    -H "Authorization: Bearer <admin-bearer-token>" \
    -H "Content-Type: application/json" \
    -d '{
          "model_name": "qwen2-7b",
          "is_system_model": true,
          "enable_warm_pool": true,
          "warm_pool_min": 1,
          "warm_pool_max": 3,
          "max_concurrent_endpoints": 8
        }'
```

### Model Directory Structure

```
/shared/models/
├── rivault-gpt-oss/          # System model
│   ├── config.json
│   ├── tokenizer.json
│   └── model-*.safetensors
├── embeddings/               # Embedding models
│   └── bge-large-en-v1.5/
└── projects/                 # Project-scoped models
    └── team-a/
        └── custom-finetuned/
```

---

## Configuration

### Environment Variables

#### Gateway

```bash
GATEWAY_ENVIRONMENT=development             # development|test|staging|production
UMRS_BASE_URL=http://localhost:8000         # UMRS endpoint
GATEWAY_LITELLM_SERVICE_KEY=sk-ll-xxxxx     # LiteLLM service key
JWT_SECRET=your-secret-key                  # JWT signing key
GATEWAY_PORT=8080                           # Gateway port
RATE_LIMIT_RPS=10                           # Rate limit per user
MAX_FETCH_BYTES=104857600                   # 100MB file fetch limit
```

Set `GATEWAY_ENVIRONMENT` to `staging` or `production` only when a real `JWT_SECRET` is present.

#### UMRS

```bash
UMRS_DATABASE_URL=postgres://user:pass@localhost:5432/umrs
UMRS_S3_ENDPOINT=http://localhost:9000
UMRS_S3_ACCESS_KEY=minioadmin
UMRS_S3_SECRET_KEY=minioadmin
UMRS_S3_BUCKET=umrs-data
UMRS_ORCHESTRATOR=slurm                     # slurm|k8s|compose
UMRS_MODEL_PATH=/shared/models
SLURM_PARTITION=gpu
SLURM_GRES=gpu:1
SLURM_TIME=04:00:00
```

#### Mini-RiVault

```bash
VLLM_MODEL_PATH=/models/rivault-gpt-oss
VLLM_MAX_MODEL_LEN=4096
VLLM_GPU_MEMORY_UTILIZATION=0.9
VLLM_TENSOR_PARALLEL_SIZE=1
VLLM_ENABLE_TOOL_CALLING=true
LITELLM_MASTER_KEY=sk-ll-xxxxx
LITELLM_PORT=11434
TRANSFORMERS_OFFLINE=1
HF_HUB_OFFLINE=1
```

---

## Hybrid Deployment Modes

The platform supports hybrid orchestration for environments that combine Kubernetes and Slurm.

### K8s-on-Slurm (Kubernetes inside Slurm jobs)

This mode is not shipped in the current tree. Use native Slurm for HPC
deployments or native Kubernetes for cluster deployments until a tested
K8s-on-Slurm runner is added with harness coverage.

### Slurm-on-K8s (Slurm jobs from Kubernetes)

**Use Case:** Kubernetes cluster submits HPC workloads to Slurm for GPU access.

```bash
helm install rivault helm/rivault/ \
    --set umrs.orchestrator=slurm \
    --set slurm.enabled=true \
    --set slurm.sshHost=slurm-login.example.com \
    --set slurm.sshKeySecretName=slurm-ssh-key
```

### Choosing the Right Mode

| Scenario | Recommended Mode |
|----------|------------------|
| HPC-primary with occasional K8s workloads | K8s-on-Slurm |
| K8s-primary needing HPC GPU access | Slurm-on-K8s |
| Pure HPC environment | Native Slurm |
| Pure cloud environment | Native K8s |
| Development/testing | Podman/Docker Compose |

---

## Security

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Internet                                │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTPS (user traffic)
              ┌────────────▼────────────┐
              │    Load Balancer / NGINX │
              └────────────┬────────────┘
                           │ HTTP (internal)
┌──────────────────────────┼──────────────────────────────────┐
│  Internal Network        │                                  │
│              ┌───────────▼───────────┐                      │
│              │       Gateway         │                      │
│              │    (auth boundary)    │                      │
│              └───────────┬───────────┘                      │
│                          │                                  │
│         ┌────────────────┴─────────────────┐                │
│         ▼                                  ▼                │
│  ┌─────────────┐                   ┌─────────────────┐      │
│  │    UMRS     │                   │  Mini-RiVault   │      │
│  └─────────────┘                   │ controlled      │      │
│                                    │ egress          │      │
│                                    └─────────────────┘      │
└─────────────────────────────────────────────────────────────┘
```

### Key Principles

1. **Controlled egress for inference containers** — `TRANSFORMERS_OFFLINE=1` / `HF_HUB_OFFLINE=1` inside mini-RiVault, proxy environment injected by launchers, and direct network blocking supplied by site policy
2. **Model downloads via UMRS durable jobs only** — never inside inference containers
3. **`project_id` from auth tokens only** — never trust user input
4. **Gateway is the auth boundary** — internal services trust Gateway headers
5. **Policies are admin-only** — `POST/PATCH/DELETE /umrs/models/policies` require admin context

### Firewall Rules

```bash
# Gateway (public-facing)
iptables -A INPUT -p tcp --dport 8080 -j ACCEPT

# Internal services (private network only)
iptables -A INPUT -p tcp --dport 8000 -s 10.0.0.0/8 -j ACCEPT
iptables -A INPUT -p tcp --dport 8000 -j DROP
```

---

## Monitoring & Operations

### Health Checks

```bash
r4 health
r4 health full
umrs ping
umrs status all

# Direct curl (debugging)
curl http://gateway:8080/health
```

### Logs

```bash
# Gateway
journalctl -u gateway -f

# UMRS
journalctl -u umrs -f

# Slurm job output
cat slurm-<job_id>.out
```

### Metrics (Prometheus)

```bash
GATEWAY_METRICS_ENABLED=true
UMRS_METRICS_ENABLED=true
```

Scrape endpoints: `http://gateway:8080/metrics`, `http://umrs:8000/metrics`

---

## Troubleshooting

### "No endpoints available"

```bash
r4 health
r4 health full
r4 health account
r4 endpoint list
r4 job list --status running
squeue --name "mini-rivault*"
r4 endpoint up --model <model-name> --wait
```

### "Model not found"

```bash
r4 model list
r4 model info <model-name>
ls -la /shared/models/<model-name>/

# Re-register
r4 model register <model-name> --path /shared/models/<model-name> --scope system --type llm
# or: umrs model register <model-name> /shared/models/<model-name> --type llm --scope system
```

### GPU errors

```bash
nvidia-smi
nvcc --version
scontrol show node | grep Gres
```

### Connection timeout

```bash
r4 health                          # user-level check
r4 model list                      # verify authenticated route

# Direct HTTP probes (operator debugging)
curl -v http://gateway:8080/health
umrs ping
curl -H "Authorization: Bearer <token>" http://gateway:8080/gw/llm/v1/models
```

### Recovery Procedures

```bash
# Restart UMRS (login-node template)
cd <site-deploy-dir>
./stop.sh --server
./start.sh --server
umrs ping

# Clean orphan resources
docker compose -f docker-compose.e2e.yaml down -v          # Compose
kubectl delete jobs -l app=mini-rivault -n rivault-system   # K8s
scancel -u $USER --name="mini-rivault*"                     # Slurm
```

---

## Maintenance Procedures

### Updating System Models

```bash
# Stage new version
r4 model stage Qwen/Qwen2.5-7B-Instruct --name qwen2-7b-v2 --scope system

# Register and provision
r4 model register qwen2-7b-v2 --path /shared/models/qwen2-7b-v2 --scope system --type llm
r4 endpoint up --model qwen2-7b-v2 --tier system --wait

# Drain old endpoint
r4 endpoint down <old-instance-id>
r4 model delete qwen2-7b-v1 --force
```

### Database Maintenance

```bash
pg_dump -h localhost -U umrs umrs > umrs_backup.sql
psql -U umrs -c "VACUUM ANALYZE;"
psql -U umrs -c "REINDEX INDEX idx_embeddings_vector;"
```

---

## CLI Quick Reference (Admin-Only)

| Category | Command | Description |
|----------|---------|-------------|
| **Bootstrap** | `umrs-server admin bootstrap` | First-run admin user + token |
| **User Management** | `umrs-server admin user add <name>` | Create user |
| | `umrs-server admin user list` | List users |
| | `umrs-server admin user show <name>` | Show user details |
| | `umrs-server admin user set-role <name> <role>` | Change role |
| | `umrs-server admin user set-slurm-account <name> <acct>` | Set Slurm account |
| | `umrs-server admin user set-slurm-account <name> <acct> --site <site>` | Set per-site Slurm account |
| | `umrs-server admin user add-to-project <name> <proj>` | Add to project |
| | `umrs-server admin user remove-from-project <name> <proj>` | Remove from project |
| | `umrs-server admin user disable <name>` | Disable user |
| | `umrs-server admin user enable <name>` | Enable user |
| | `umrs-server admin user delete <name> [--purge]` | Delete user |
| **Admin Tokens** | `umrs-server admin token generate <name>` | Generate admin token |
| | `umrs-server admin token generate-jwt [--user U] [--project P]` | Generate Gateway JWT |
| | `umrs-server admin token list` | List tokens |
| | `umrs-server admin token revoke <name>` | Revoke token |
| **Slurm** | `umrs-server admin slurm accounts` | List Slurm accounts |
| **Daemon** | `umrs-server admin reload` | Hot-reload user records |
| **System Models** | `r4 model stage <hf-repo> --scope system` | Seed system pool |
| | `r4 model register <name> --scope system` | Register path into system pool |
| | `umrs model register <name> <path> --scope system` | Register via UMRS CLI |
| **Model Policies** | `POST /umrs/models/policies` | Create policy (REST API) |

For shared user commands (`r4 health`, `r4 model list`, `r4 job list`, `r4 endpoint up`, etc.), see the [CLI Reference](../reference/cli.md).

---

## Related Documentation

### Deployment Guides
- [HPC Multi-Node Deployment](hpc-deployment.md) - Standard Ansible deployment (recommended)
- [Docker Deployment Guide](docker-deployment.md) - Podman/Docker Compose for development
- [Kubernetes Deployment Guide](kubernetes-deployment.md) - Production K8s with Helm

### User Documentation
- [Basic User Quick Start](../quickstart.md) - User setup for shared deployments
- [R4 Documentation Index](../index.md) - End user documentation map
- [CLI Reference](../reference/cli.md) - CLI command reference
- [SDK Reference](../reference/sdk.md) - Python SDK documentation

### Architecture And Packaging
- [Library Group Structure](../../architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md) - Package ownership and deploy install sets
- Server/Client Deployment - Shared server and thin client split

### Technical References
- [Testing Guide](../../../TESTING.md) - Comprehensive testing documentation
- [E2E Testing Guide](../../../tests/E2E_TESTING.md) - End-to-end test setup
- DEVELOPMENT_OBJECTIVES.md - Development roadmap
