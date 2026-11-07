# HPC Quick Start: Deploy R4 on a Slurm GPU Cluster

A step-by-step guide for deploying the R4 Platform on an HPC cluster with Slurm.

> **Deployment tooling (2026-11-07).** The Ansible deployment of the R-CCS
> sites (`deploy/ansible` and its site inventories) is kept in the development
> repository and is not part of the public tree. Use
> [deploy/standalone](../../../deploy/standalone/README.md) for a single host,
> or start from the UMRS login-node template in
> `umrs/deploy/templates/login-node/` (its `README.md`, `site.yaml`,
> `env.example`, `start.sh`, `stop.sh`) and drive it with your own site
> tooling. Steps 2–4 and Teardown below follow that template.

> **Legend**
>
> | Tag | Meaning |
> |-----|---------|
> | 🖥 **Admin / Local** | Run on your laptop (macOS/Linux) |
> | 🔑 **Admin / Login Node** | Run on the HPC login node (via SSH) |
> | 👤 **User / Local** | Run by the end user on their own machine |
> | 👤 **User / Login Node** | Run by the end user on the HPC login node |

---

## Overview

```
┌─ Your Laptop ──────────────────────────────────────────────┐
│  r4 CLI  ── SSH tunnel ──▶  Gateway (:8080)                │
└────────────────────────────┼───────────────────────────────┘
                             │
┌─ HPC Login Node ───────────┼───────────────────────────────┐
│                            ▼                               │
│  Gateway (:8080)  →  UMRS server (:8000, ZMQ :5555)        │
│                      PostgreSQL (:5432)  MinIO (:9000)     │
└────────────────────────────┼───────────────────────────────┘
                             │ sbatch
┌─ GPU Compute Node ─────────┼───────────────────────────────┐
│                            ▼                               │
│  Mini-RiVault (vLLM :8000, LiteLLM :8001)                  │
│  (Podman-based sites: Slurm + Podman wrapper launch)        │
│  (other clusters may use Singularity/Apptainer instead)     │
│  (model weights mounted read-only from shared filesystem)   │
└────────────────────────────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Where |
|-------------|-------|
| Python 3.10+ | Login node |
| Podman 4.0+ (rootless) | Login node; also compute nodes for Podman-based sites |
| Singularity / Apptainer 3.8+ (optional) | Compute nodes for sites using Singularity/Apptainer instead of Podman |
| Slurm (sbatch, squeue, scancel) | Login node |
| SSH access to login node | Your laptop |
| GPU model weights on shared filesystem (e.g. Lustre) | Compute nodes |

---

## Step 1 — Clone and Prepare (Admin / Local)

> 🖥 **Admin / Local**

```bash
git clone --recurse-submodules https://github.com/R-CCS-HPBD/r4.git
cd r4
```

---

## Step 2 — Configure the Site (Admin / Login Node)

> 🔑 **Admin / Login Node**

Copy the UMRS login-node template into a site directory of your own and fill
in its placeholders:

```bash
cp -r umrs/deploy/templates/login-node umrs/deploy/my-cluster
cd umrs/deploy/my-cluster
cp env.example .env
$EDITOR .env        # POSTGRES_PASSWORD, S3_ROOT_USER / S3_ROOT_PASSWORD, ...
$EDITOR site.yaml   # Slurm partition and account, model-weight paths
```

Set in `site.yaml` and `.env`:

- Slurm partitions available for GPU jobs
- Path to pre-staged model weights on the shared filesystem
- Ports (defaults: Gateway 8080, UMRS 8000, ZMQ 5555, MinIO 9000)

Never commit `.env`.

---

## Step 3 — Deploy Services (Admin / Login Node)

> 🔑 **Admin / Login Node**

Install UMRS into a Python 3.10+ venv first (`pip install -e umrs/`), then:

```bash
cd umrs/deploy/my-cluster
./start.sh
```

**What this does:**

1. Starts **PostgreSQL** and the S3 object store as rootless Podman containers
2. Starts the **UMRS server** natively (needs the Slurm CLI on `PATH`)

Start the **Platform Gateway** in front of the UMRS server with your site
tooling (for example as a Podman container). If you manage several hosts, wrap
these steps in your own configuration management; the template documents the
shape of the result.

On Podman-based sites the compute-side runtime is the Slurm + Podman harness
behind `site.yaml` and the site's model-serving Slurm script.
Singularity/Apptainer is an alternative for other clusters.

---

## Step 4 — Open SSH Tunnel (Admin / Local)

> 🖥 **Admin / Local**

If your laptop cannot reach the login node ports directly, open an SSH tunnel:

```bash
ssh -N -L 8080:127.0.0.1:8080 -L 5555:127.0.0.1:5555 -L 9000:127.0.0.1:9000 my-login-node
```

This forwards to `localhost`:

| Port | Service |
|------|---------|
| 8080 | Platform Gateway |
| 5555 | ZMQ control plane |
| 9000 | MinIO |

---

## Step 5 — Bootstrap Admin Account (Admin / Login Node)

> 🔑 **Admin / Login Node** — SSH into the login node for this step.

This is a **one-time** step that creates the first admin user and prints an admin token.

```bash
ssh my-login-node
cd <path-to-r4>/umrs/deploy/my-cluster

# Activate the server venv
source .venv/bin/activate

# Create admin user + first token
umrs-server admin bootstrap --admin-user admin --json
```

**Save the token** — it is shown only once at creation time.

This token is for UMRS operator/admin actions only. Normal users access the
Gateway with JWTs, not with this admin token.

> **Already bootstrapped?** The command is idempotent. If it returns
> `"token_created": false` and `"token": null`, the admin user and token
> already exist. Generate a fresh token instead:
>
> ```bash
> umrs-server admin token generate my-admin --description "admin token"
> ```

---

## Step 6 — Register a System Model (Admin / Login Node)

> 🔑 **Admin / Login Node**

If model weights are already on the shared filesystem:

```bash
# Register a pre-staged model
r4 model register qwen2-7b \
    --path /shared/models/Qwen2.5-7B-Instruct \
    --scope system \
    --type llm
```

If model weights need to be downloaded from HuggingFace:

```bash
# Download and register (runs as a UMRS durable job)
r4 model stage Qwen/Qwen2.5-7B-Instruct \
    --name qwen2-7b \
    --scope system \
    --type llm
```

> **Offline / air-gapped clusters**: Download on a machine with internet access, then
> `rsync` to the shared filesystem and use `r4 model register`.

---

## Step 7 — Bring Up a System Endpoint (Admin)

> 🔑 **Admin / Login Node** or 🖥 **Admin / Local** (if tunnel is active)

The model name must match a registered model (check with `r4 model list`):

```bash
r4 endpoint up --model qwen2-7b --wait
```

This submits a Slurm job that launches a mini-RiVault (vLLM + LiteLLM) on a GPU compute node. `--wait` blocks until the endpoint is ready.

Check status:

```bash
r4 endpoint list
```

---

## Step 8 — Verify Everything Works (Admin)

> 🖥 **Admin / Local** (with tunnel) or 🔑 **Admin / Login Node**

```bash
# Health check
r4 health

# Quick inference test
r4 chat "Hello, what model are you?"
```

---

## User Onboarding

Once the platform is running, onboard users with these steps.

### Step A — Create User Account (Admin / Login Node)

> 🔑 **Admin / Login Node**

```bash
umrs-server admin user add alice \
    --fullname "Alice Smith" \
    --role user \
    --slurm-account gpu-partition \
    --project team-alpha
```

### Step B — Generate Gateway JWT for User (Current Dev Mode)

> 🔑 **Admin / Login Node**

```bash
export JWT_SECRET='same-secret-configured-in-gateway'

umrs-server admin token generate-jwt \
  --user alice \
  --project team-alpha \
  --days 30
```

**Send the JWT to Alice** through a secure channel. It is shown only once.

Gateway JWTs created with `umrs-server admin token generate-jwt` are stateless and
expire naturally. They are not stored in `admin_tokens.json`, so there is no
matching `revoke` subcommand for them.

In the current dev flow, this JWT is intentionally project-scoped. The Gateway
uses the `project_id` claim in the token as the caller's active project scope.
If Alice needs to work in a different project, issue a different JWT for that
project.

> In production with OIDC/Okta enabled, users should authenticate with `r4 auth login --oidc`
> instead of receiving locally generated JWTs. The target shared-deployment model
> is one user login session plus server-side project membership checks, not one
> manually generated JWT per project.

### Step C — Install r4 CLI (User / Local)

> 👤 **User / Local**

```bash
# Option 1: Install script
./scripts/setup_r4_client.sh

# Option 2: pip
pip install -e ./r4
```

### Step D — Configure r4 Profile (User / Local)

> 👤 **User / Local**

```bash
r4 auth login \
    --gateway-url http://localhost:8080 \
    --api-key <jwt-from-admin> \
    --project team-alpha \
    --model qwen2-7b
```

To view or change the saved local project/user context later:

```bash
r4 config context
r4 config context list
r4 config context --project team-alpha --user alice
```

If using OIDC instead of API keys:

```bash
r4 auth login \
    --gateway-url http://localhost:8080 \
    --oidc \
    --project team-alpha \
    --model qwen2-7b
```

With OIDC/Okta, the intended direction is that users can change projects within
the same login session and the platform re-checks project membership server-side.
Use the project-scoped `generate-jwt` flow above only for current dev/testing or
for narrowly scoped automation credentials.

### Step E — Open SSH Tunnel (User / Local)

> 👤 **User / Local**

If the user is working from a remote machine (not the login node):

```bash
ssh -N -L 8080:localhost:8080 my-login-node
```

### Step F — Verify (User / Local)

> 👤 **User / Local**

```bash
r4 health
r4 health account
r4 chat "Hello!"
```

---

## User Management Reference

All user management commands are run by the admin on the login node.

> 🔑 **Admin / Login Node**

### List Users

```bash
umrs-server admin user list
umrs-server admin user list --role admin --json
```

### Show User Details

```bash
umrs-server admin user show alice
```

### Change User Role

```bash
# Promote to admin
umrs-server admin user set-role alice admin

# Demote to regular user
umrs-server admin user set-role alice user
```

### Manage Project Membership

```bash
umrs-server admin user add-to-project alice team-beta
umrs-server admin user remove-from-project alice team-alpha
```

### Change Slurm Account

```bash
# Global fallback account
umrs-server admin user set-slurm-account alice hpc-premium

# Per-site account (multi-cluster deployments)
umrs-server admin user set-slurm-account alice hpc-premium --site cluster-a
umrs-server admin user set-slurm-account alice gpu-group --site cluster-b
```

### Disable / Enable User

```bash
# Temporarily block access (preserves history)
umrs-server admin user disable alice

# Re-enable
umrs-server admin user enable alice
```

### Delete User

```bash
# Soft delete (default, preserves audit trail)
umrs-server admin user delete alice

# Hard delete (removes user file)
umrs-server admin user delete alice --purge -y
```

### Admin Token Management

```bash
# Admin/operator tokens only
umrs-server admin token list
umrs-server admin token revoke my-admin -y
```

---

## Teardown

> 🔑 **Admin / Login Node**

```bash
cd umrs/deploy/my-cluster
./stop.sh           # stops the native UMRS server and the infra containers
```

Stop the Gateway with your site tooling, cancel any remaining endpoint jobs
(`squeue -u $USER`, `scancel <job-id>`), and close the SSH tunnel on your
laptop.

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| `r4 health` fails | Is the SSH tunnel open? |
| Endpoint stuck in "provisioning" | `squeue -u $USER` — is the Slurm job pending? Check partition availability. |
| Gateway returns 401 | Verify `GATEWAY_LITELLM_SERVICE_KEY` in Gateway container matches the UMRS config. |
| Podman container won't start | On shared login nodes, rootless Podman may need a stable `runRoot`. Check `podman info`. |
| `umrs-server admin` command not found | Activate the server venv: `source .venv/bin/activate` |
| Model not found by endpoint | Did you `r4 model register` with `--scope system`? Check `r4 model list` and make sure `r4 endpoint up --model <name>` uses that registered name. |
