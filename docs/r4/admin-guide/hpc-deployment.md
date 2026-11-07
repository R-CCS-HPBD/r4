# HPC Multi-Node Deployment Guide

This guide covers deploying RiVault to HPC clusters with multiple login nodes and compute nodes using Ansible automation.

> **Not in the public tree (2026-11-07).** The Ansible playbooks this page
> describes (`deploy/ansible`), and the Ansible deployment of the R-CCS sites,
> are kept in the development repository and are not part of the public
> repository. With the public repository, use
> [deploy/standalone](../../../deploy/standalone/README.md) for a single host,
> or start from the UMRS templates in `umrs/deploy/templates/` (`login-node/`,
> `frontend-multi-hpc/`) with your own site tooling. The rest of this page
> describes the shape of a multi-node deployment and is kept as reference.

This page documents the generic multi-node Ansible path. In that flow, compute nodes are provisioned for Singularity/Apptainer-style launches. Sites that run steps with Slurm + rootless Podman use a userland harness instead (see `umrs/deploy/templates/login-node/`).

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           HPC Cluster                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    Login Nodes (1..n)                                │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │   │
│  │  │   Login Node 1  │  │   Login Node 2  │  │   Login Node n  │      │   │
│  │  │   (Primary)     │  │   (Secondary)   │  │   (Secondary)   │      │   │
│  │  │                 │  │                 │  │                 │      │   │
│  │  │  • Gateway      │  │  • Gateway      │  │  • Gateway      │      │   │
│  │  │  • UMRS         │  │  • UMRS         │  │  • UMRS         │      │   │
│  │  │  • PostgreSQL   │  │                 │  │                 │      │   │
│  │  │  • MinIO        │  │                 │  │                 │      │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘      │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                                    │ Slurm Job Submission                   │
│                                    ▼                                        │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                  Compute Nodes (1..m, m ≥ n)                         │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │   │
│  │  │  GPU Node 1  │  │  GPU Node 2  │  │  GPU Node m  │  ...          │   │
│  │  │  A100 x 4    │  │  A100 x 4    │  │  V100 x 2    │               │   │
│  │  │              │  │              │  │              │               │   │
│  │  │ • Singularity│  │ • Singularity│  │ • Singularity│               │   │
│  │  │ • vLLM       │  │ • vLLM       │  │ • vLLM       │               │   │
│  │  │ • LiteLLM    │  │ • LiteLLM    │  │ • LiteLLM    │               │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                    │                                        │
│                    Shared Filesystem (NFS/Lustre/GPFS)                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │   /shared/models    /shared/data    /shared/singularity_cache       │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

Diagram note: the compute-node boxes above show the generic Singularity runtime. On Slurm + Podman sites, compute-side Mini-RiVault jobs are launched through the site-owned Slurm/Podman wrapper instead.

## Prerequisites

### Software Requirements

| Node Type | Requirements |
|-----------|--------------|
| Deployment Host | Ansible 2.15+, SSH access |
| Login Nodes | Python 3.8+, Podman or Docker |
| Compute Nodes | NVIDIA drivers, CUDA 12+, Singularity/Apptainer 4+ (generic profile) |

### Hardware Requirements

| Node Type | Minimum | Recommended |
|-----------|---------|-------------|
| Login Node | 4 cores, 16GB RAM | 8 cores, 32GB RAM |
| Compute Node | 8 cores, 64GB RAM, 1 GPU | 16+ cores, 128GB+ RAM, 4+ GPUs |

### Network Requirements

- Login nodes accessible via SSH from deployment host
- Internal network between login and compute nodes
- Optional: Internet access on login nodes for model downloads

## Quick Start

### 1. Get the Ansible Playbooks

The playbooks live in `deploy/ansible` of the development repository. The
public repository (`https://github.com/R-CCS-HPBD/r4.git`) does not ship them;
see the note at the top of this page.

### 2. Configure Inventory

```bash
# Copy the template and edit it
cp inventory/hosts.yaml.template inventory/hosts.yaml
$EDITOR inventory/hosts.yaml
```

The compute-node sections below describe the Singularity flow; they are not
requirements for Slurm + Podman sites.

**Minimal inventory example:**

```yaml
all:
  vars:
    ansible_user: deploy
    ansible_ssh_private_key_file: ~/.ssh/id_rsa
    
  children:
    login_nodes:
      hosts:
        login01.hpc.local:
          primary: true
          
    compute_nodes:
      hosts:
        gpu01.hpc.local:
          gpu_count: 4
          gpu_type: nvidia_a100
```

### 3. Run Deployment

```bash
# Make bootstrap script executable
chmod +x bootstrap.sh

# Full deployment
./bootstrap.sh

# Or with verbose output
./bootstrap.sh -v
```

These playbooks provision the login and compute-node prerequisites around the
platform. UMRS remains the owner of endpoint lifecycle, readiness polling,
retry semantics, and endpoint registry state once the stack is running.

## Deployment Options

### Deploy Login Nodes Only

```bash
./bootstrap.sh --login
```

### Deploy Compute Nodes Only

```bash
./bootstrap.sh --compute
```

### Deploy Specific Nodes

```bash
./bootstrap.sh --limit gpu01.hpc.local
```

### Dry Run (Check Mode)

```bash
./bootstrap.sh --check
```

## Configuration

### Global Settings

Edit `group_vars/all.yaml`:

```yaml
# Version
rivault_version: "latest"

# Shared filesystem (must be accessible from all nodes)
shared_filesystem: "/shared"
model_storage_path: "{{ shared_filesystem }}/models"

# Slurm settings
slurm_managed_externally: true
slurm_partition: "gpu"

# Security
litellm_master_key: "sk-ll-your-secret-key"
postgres_password: "your-db-password"
```

### Login Node Settings

Edit `group_vars/login_nodes.yaml`:

```yaml
# Service ports (generic profile)
gateway_port: 8080
umrs_port: 8000
litellm_port: 8001

# Container runtime
container_runtime_login: "podman"

# Optional features
enable_n8n: false
enable_observability: true
```

### Compute Node Settings

Edit `group_vars/compute_nodes.yaml`:

```yaml
# vLLM configuration
vllm_max_model_len: 4096
vllm_tensor_parallel_size: 1

# Singularity settings (generic profile)
singularity_nvidia: true

# Offline mode (CRITICAL for security)
inference_env:
  TRANSFORMERS_OFFLINE: "1"
```

## Secrets Management

Use Ansible Vault for sensitive data:

```bash
# Create encrypted secrets file
ansible-vault create group_vars/secrets.yaml
```

**secrets.yaml:**
```yaml
vault_postgres_password: "secure-password-here"
vault_minio_secret_key: "minio-secret-here"
vault_litellm_master_key: "sk-ll-production-key"
vault_grafana_password: "grafana-admin-pass"
```

**Reference in playbook:**
```yaml
# In group_vars/all.yaml
postgres_password: "{{ vault_postgres_password }}"
```

**Run with vault:**
```bash
./bootstrap.sh --ask-vault-pass
# or store it in a file without putting it on the command line
# (read -s keeps it out of shell history and the terminal):
read -rs VAULT_PASS && printf '%s' "$VAULT_PASS" > .vault_pass && unset VAULT_PASS
chmod 600 .vault_pass
./bootstrap.sh --vault-password-file .vault_pass
```

## Multi-Node Scenarios

### Scenario 1: Single Login + Multiple Compute

```yaml
login_nodes:
  hosts:
    login01:
      primary: true
      
compute_nodes:
  hosts:
    gpu[01:10]:
      gpu_count: 4
      gpu_type: nvidia_a100
```

### Scenario 2: HA Login Nodes

```yaml
# In group_vars/login_nodes.yaml
ha_enabled: true
ha_vip: "10.0.0.100"

# In inventory
login_nodes:
  hosts:
    login01:
      primary: true
    login02:
      primary: false
```

### Scenario 3: Heterogeneous GPUs

```yaml
compute_nodes:
  hosts:
    # A100 nodes for large models
    gpu[01:05]:
      gpu_count: 8
      gpu_type: nvidia_a100
      gpu_memory_gb: 80
      model_preference:
        - "Qwen/Qwen2.5-72B-Instruct"
        
    # V100 nodes for smaller models
    gpu[06:20]:
      gpu_count: 4
      gpu_type: nvidia_v100
      gpu_memory_gb: 32
      model_preference:
        - "Qwen/Qwen2.5-7B-Instruct"
```

## Integration with Existing Infrastructure

### Existing Slurm Installation

If Slurm is already managed externally:

```yaml
# In group_vars/all.yaml
slurm_managed_externally: true
slurm_partition: "gpu"  # Your existing partition
```

### Existing PostgreSQL

```yaml
# In group_vars/login_nodes.yaml
postgres_external: true
postgres_host: "db.hpc.local"
postgres_port: 5432
```

### Existing Container Registry

```yaml
# In group_vars/all.yaml
container_registry: "registry.hpc.local:5000"
container_registry_auth: true
```

## Post-Deployment

### Stage Models

This seeds the shared system pool. Project- and user-scope models are
self-service through the `r4 model` commands documented in the [Quick Start](../quickstart.md) and [CLI Reference](../reference/cli.md).

```bash
# Connect to login node
ssh login01

# Stage a model into the system pool (admin only)
r4 model stage Qwen/Qwen2.5-7B-Instruct --scope system

# Check model status
r4 model list --scope system
```

### Test Inference

```bash
# Quick chat test
r4 chat "Hello, what is 2+2?"

# API test
curl -X POST http://login01:8080/gw/llm/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test-key" \
  -d '{"model": "rivault-model", "messages": [{"role": "user", "content": "Hi"}]}'
```

### Monitor Services

```bash
# Generic profile: Gateway HTTP health
curl http://login01:8080/health

# Generic profile: UMRS HTTP health
curl http://login01:8000/health

# Direct control-plane probe
umrs ping

# View Grafana dashboard
open http://login01:3000
```

## Troubleshooting

### SSH Connectivity Issues

```bash
# Test connectivity
./bootstrap.sh --connectivity

# Debug SSH
ssh -vvv deploy@login01
```

### Inventory Validation

```bash
ansible-inventory -i inventory/hosts.yaml --list
ansible-inventory -i inventory/hosts.yaml --graph
```

### Role-Specific Debug

```bash
# Run a single role with debug
ansible-playbook -i inventory/hosts.yaml playbooks/site.yaml \
  --tags login \
  --start-at-task "Deploy UMRS server" \
  -vvv
```

### GPU Issues

```bash
# On compute node
nvidia-smi
singularity exec --nv docker://hello-world nvidia-smi
```

### Container Issues

```bash
# On login node
podman ps -a
podman logs gateway
podman logs umrs
```

## Teardown

Remove RiVault from the cluster:

```bash
# Standard teardown (keeps data)
./bootstrap.sh --teardown
```

## Security Considerations

1. **Offline Inference**: Compute nodes run with `TRANSFORMERS_OFFLINE=1`
2. **Model Downloads**: Only UMRS download jobs have internet access
3. **API Keys**: Use Ansible Vault for all secrets
4. **Network Isolation**: Internal network only between components
5. **Audit Logging**: All API calls are logged via Gateway

## Related Documentation

- [Main README](../../../README.md)
- [Docker Deployment](docker-deployment.md)
- [Kubernetes Deployment](kubernetes-deployment.md)
- Ansible README
- Development Instructions
