# R4 Platform Contract

> Audience: **operators** deploying R4 and **users** consuming it.
> For contributor-level architecture see [ARCHITECTURE.md](ARCHITECTURE.md).
> For egress infrastructure details see docs/admin/security/EGRESS_INFRASTRUCTURE.md.

> **2026-09-22 (design reset):** this contract states the guarantees of the
> *current implementation*. The target design is
> [docs/R4-UMRS-DESIGN.md](docs/R4-UMRS-DESIGN.md); where the two differ
> (single ZeroMQ transport, UMRS as the only gateway, storage never reachable
> by clients, one UMRS hub per institution, universal Podman sandboxes) the
> gap row is in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md).
> Rows below that name "Gateway", "MinIO" or "Singularity/Apptainer" describe
> today's edge, the pre-ADR-055 object store on non-front profiles, and the
> legacy compute path respectively.

## Purpose

This document lists what the R4 platform guarantees, what requires site
configuration to hold, and what is explicitly unsupported. Operators
who have never read the source code should be able to determine which
invariants are active on their deployment and which require additional
infrastructure work.

---

## Guarantee Levels

| Level | Meaning |
|---|---|
| **Unconditional** | Enforced by application code. Holds on every deployment that runs the released software unchanged. |
| **Site-dependent** | Requires infrastructure configuration by the site operator. The application provides the hooks but cannot enforce the guarantee alone. |
| **Unsupported** | The platform does not provide or claim to provide this capability. Building on it will break. |

---

## Invariants

### 1. Execution Authority

| # | Statement | Level |
|---|---|---|
| 1.1 | Every job has exactly one `execution_authority` (`slurm` \| `k8s` \| `compose`), chosen at creation, never changed. | Unconditional |
| 1.2 | Only UMRS may submit jobs to Slurm or Kubernetes. No other component issues `sbatch` or creates K8s Job resources. | Unconditional |
| 1.3 | Job outputs are attempt-scoped (`s3://…/jobs/<job_id>/attempt_<n>/…`). Only the committed attempt's outputs are visible to readers. | Unconditional |

### 2. Model Handling

| # | Statement | Level |
|---|---|---|
| 2.1 | Models are pre-staged on the filesystem or downloaded via explicit UMRS durable jobs. Models are never downloaded at inference time inside mini-RiVault containers. | Unconditional |
| 2.2 | Model download jobs and `KNOWLEDGE_INGEST` jobs are the only jobs with controlled egress. Both flow through EgressGuard with domain allowlists. See ADR-015. | Unconditional |
| 2.3 | Inference containers set `TRANSFORMERS_OFFLINE=1` and must fail fast if network access is attempted. | Unconditional |
| 2.4 | Model names are static (`rivault-gpt-oss-static`); dynamic model naming is not supported. | Unconditional |

### 3. Network Egress

| # | Statement | Level |
|---|---|---|
| 3.1 | All mini-RiVault service launch paths inject EgressGuard proxy environment variables (`HTTP_PROXY`, `HTTPS_PROXY`, `NO_PROXY`). | Unconditional |
| 3.2 | K8s deployments enforce a fail-closed `NetworkPolicy` that allows only DNS and EgressGuard egress. | Unconditional |
| 3.3 | The NGINX forward proxy (`out-nginx.conf`) rejects hosts not on the S3 bucket allowlist. | Unconditional |
| 3.4 | **Direct TCP egress from compute nodes is blocked.** | **Site-dependent** |

> **Operator action for 3.4:** On Slurm sites, the application injects proxy
> env vars but **cannot block direct TCP connections**. Blocking direct egress
> requires iptables/nftables rules, cgroup network containment, or network
> namespace isolation on compute nodes. See
> docs/admin/security/EGRESS_INFRASTRUCTURE.md for
> configuration guidance. Without this, a process inside the container could
> bypass the proxy by connecting directly to an external IP.

### 4. Authentication and Routing

| # | Statement | Level |
|---|---|---|
| 4.1 | All user traffic enters through the Platform Gateway. Users never connect directly to vLLM, LiteLLM, or UMRS. | Unconditional |
| 4.2 | The Gateway validates JWT tokens on every request. Invalid or expired tokens are rejected with 401. | Unconditional |
| 4.3 | The auth sidecar refuses to start with the built-in dev JWT secret when `GATEWAY_ENVIRONMENT` is not `development`/`test` and `GATEWAY_HOST` is not loopback. | Unconditional |
| 4.4 | Gateway is stateless. All retries happen in the `r4` SDK. Gateway never retries requests internally. | Unconditional |
| 4.5 | UMRS never calls LiteLLM for user inference — it only provisions, probes readiness, and registers endpoints. | Unconditional |

### 5. Endpoint Lifecycle

| # | Statement | Level |
|---|---|---|
| 5.1 | Endpoint readiness is determined by authoritative polling at `/v1/models` on the LiteLLM port. | Unconditional |
| 5.2 | The Janitor runs periodic liveness checks. Endpoints whose backing job has terminated are pruned from the active registry. | Unconditional |
| 5.3 | The endpoint list API (`/umrs/endpoints/active`) filters out ghost endpoints at request time. | Unconditional |

### 6. Data Isolation

| # | Statement | Level |
|---|---|---|
| 6.1 | Files are accessed only via presigned S3 URLs. vLLM/LiteLLM never receive direct file uploads. | Unconditional |
| 6.2 | Presigned URLs are never issued to external clients. They exist solely so compute nodes can reach S3 without holding MinIO credentials. | Unconditional |
| 6.3 | Gateway performs presigned URL fetches to centralize auditing, size enforcement, and PII control. | Unconditional |
| 6.4 | RAG indexing and retrieval run **inside sandboxed containers** (mini-RiVault pattern). The sandbox may be co-located with inference on compute nodes OR a standalone sandboxed service on the frontend/login node. See ADR-013. | Unconditional |
| 6.5 | MCP servers run **inside sandboxed containers** (mini-RiVault pattern). Same deployment flexibility as RAG. See ADR-013. | Unconditional |

### 7. Container Runtime

| # | Statement | Level |
|---|---|---|
| 7.1 | The platform abstracts container runtime (`docker` \| `podman` \| `singularity`). Runtime selection is environment-driven. | Unconditional |
| 7.2 | Default runtime on HPC is Singularity/Apptainer. Docker is for development only. | Unconditional |
| 7.3 | **Singularity/Apptainer is installed and available on compute nodes.** | **Site-dependent** |

> **Operator action for 7.3:** UMRS submits `singularity exec` commands
> inside Slurm job scripts. If Singularity is not available, endpoint
> provisioning will fail. Operators must ensure the container runtime
> is installed and the SIF image is pre-staged.

### 8. ZMQ Connectivity and Transport Security

| # | Statement | Level |
|---|---|---|
| 8.1 | When `UMRS_TUNNEL_MODE=true`, UMRS binds control/data ZMQ sockets to `127.0.0.1` only. Binding `0.0.0.0` in tunnel mode is forbidden. | Unconditional |
| 8.2 | `UMRS_TUNNEL_MODE` is a **connectivity** mechanism. It is required on HPC clusters where ZMQ ports are firewall-blocked and SSH port-forwarding is the only available TCP path to the server. It is not required for direct-connect deployments (k8s, cloud VPC) where ZMQ ports are routable. | Unconditional |
| 8.3 | **SSH tunnel is established and maintained between the client and the UMRS host.** | **Site-dependent** |
| 8.4 | When `UMRS_TUNNEL_MODE=true`, the HTTP sidecar MUST bind to `127.0.0.1`. The `UMRS_HTTP_HOST` env var is clamped to loopback in tunnel mode. | Unconditional |
| 8.5 | When `UMRS_ZMQ_AUTH_TYPE=curve`, UMRS ZMQ sockets MUST use ZMTP CURVE keys before `bind()`/`connect()`. Server sockets require trusted client key material through ZAP (`UMRS_ZMQ_CURVE_CLIENT_KEYS_DIR`) unless the explicit development-only allow-any flag is set. | Unconditional |
| 8.6 | CURVE authentication hardens direct ZMQ transport but does not replace Gateway policy, audit, rate limiting, or UMRS command authorization. | Unconditional |
| 8.7 | Tunnel mode and CURVE serve complementary roles. Tunnel mode solves connectivity (bind address); CURVE solves cryptographic authentication. Direct-connect deployments SHOULD enable CURVE (`UMRS_ZMQ_AUTH_TYPE=curve`). HPC SSH-tunnel deployments MAY also enable CURVE for defense-in-depth. | Advisory |

### 9. Knowledge Ingest Egress

| # | Statement | Level |
|---|---|---|
| 9.1 | Knowledge source acquisition from remote locations (`http_url`, `git_repo`) executes ONLY inside `KNOWLEDGE_INGEST` jobs of the egress-enabled class (same execution authority as model downloads). | Unconditional |
| 9.2 | All knowledge egress flows through EgressGuard. The domain allowlist (`knowledge_ingest.allowed_domains` in site.yaml) controls permitted destinations. Default empty = all knowledge egress disabled. | Unconditional |
| 9.3 | Client-side acquisition of remote knowledge sources is forbidden. The `--local-fetch` developer flag is the only exception and requires explicit acknowledgement. | Unconditional |
| 9.4 | Knowledge ingest egress is audit-logged with: user_id, source_url, egress_action, content_scan_result, bytes_downloaded. Audit retention is 90 days. | Unconditional |
| 9.5 | Knowledge ingest jobs perform content scanning against configurable block_patterns (PII, SQL, credentials). Matching content is blocked before staging. | Site-dependent |

---

## Explicitly Unsupported

The following are **not** provided by the platform and must not be
assumed:

| Item | Reason |
|---|---|
| SSL/TLS between internal components | Internal traffic is HTTP only. TLS termination happens at the ingress boundary (if any). The per-profile plaintext boundary is documented in §"Channel boundary per profile" below — plaintext is confined to single-host loopback/compose networks; anything crossing a host boundary is encrypted (HTTPS or SSH) or refused. |
| Internet access from inference containers | Violates invariant 2.3 and 3.1. KNOWLEDGE_INGEST and MODEL_DOWNLOAD jobs have egress via EgressGuard only (ADR-015). |
| Dynamic model naming | Violates invariant 2.4. |
| Direct file uploads to vLLM | Violates invariant 6.1. |
| Langfuse integration | Not integrated; observability uses OpenTelemetry. |
| Multi-tenant GPU sharing within a single mini-RiVault | Each mini-RiVault owns its allocated GPU exclusively. |

### Channel boundary per profile (D-05, 2026-09-23)

The "no plaintext channel" design rule is honored **per deployment
profile** by confining plaintext HTTP to single-host fabrics and
encrypting or refusing everything that crosses a host boundary. This is
the documented boundary of record; it was verified live on umrs-front
after the 2026-09-23 clean converge.

**umrs-front (single hub host)** — all server-side components share one
host; plaintext HTTP appears only on fabrics that never leave it:

| Channel | Fabric | Boundary class |
|---|---|---|
| Client/browser → Apache | `https://…:443` | **Encrypted (TLS)** — the only external entry |
| Apache → gateway nginx (50080-5008x) | host loopback (`127.0.0.1`) | Single-host loopback |
| Gateway nginx → auth sidecar → umrs-server | container compose network (one host) | Single-host fabric |
| umrs-server → postgres / seaweedfs | `127.0.0.1` published ports (one host) | Single-host loopback |
| r4 client ↔ umrs-server data plane | ZeroMQ, CURVE-encrypted in session mode (`umrs_front_zmq_auth_type: session`) | **Encrypted (CURVE)** |
| umrs-front → cluster login nodes | SSH (ControlMaster, key auth) | **Encrypted (SSH)** |
| Venue worker sandbox → gateway | session-token HTTPS to the host gateway (ADR-046 rail) | **Encrypted (TLS)** |

**Exposure rule (2026-11-07):** Apache TLS on 443 is the only external entry. Every other listener
on the front host binds to loopback or is filtered by the host firewall; the one intended exception is
the CURVE-encrypted ZeroMQ range clients connect to directly. The gateway nginx listeners bind
`127.0.0.1` (`umrs_front_gateway_loopback_only: true`), and the browser session cookie is `Secure`.

**Cluster login nodes (native server)** — the native `umrs-server`
binds ZMQ on `localhost` (client tools on the same login node; the
workstation reach is via the gateway's HTTPS edge, not raw ZMQ across
the WAN). Cross-host database/object-store traffic does not exist on
this profile — postgres/seaweedfs are front-host services reached
through the gateway.

**Venue worker sandboxes** — egress is allowlist-guarded (EgressGuard)
and the rail rides the encrypted gateway path; plaintext HTTP inside
the sandbox's own pod network never crosses the host boundary.

Unsupported remains unsupported: any deployment that moves an internal
component **off** the single-host fabric without TLS is a contract
violation, not a configuration choice.

### 7. A2L conservation domain (ADR-034)
The standalone A2L service, its Ansible role and feature flag were removed
2026-09-11; A2L is the bounded module `umrs.a2l` inside UMRS
(`umrs/src/umrs/a2l/`, routes `umrs/src/umrs/api/a2l.py` under `/umrs/a2l`).

| # | Statement | Level |
|---|---|---|
| 7.1 | The authority for A2L records (artifact identities, lineage edges, annotations, operator decisions) is UMRS Postgres, owned by `umrs.a2l`. No second store. | Unconditional |
| 7.2 | Clients reach A2L only through UMRS routes behind the gateway (`/gw/…` → `/umrs/a2l/*`); there is no separate A2L host, port, or URL to configure. | Unconditional |
| 7.3 | `.kb` v1.x exports keep the `a2l_refs` section mapping UMRS artifact_ids → A2L identifiers. Backward-compatible. | Unconditional |
| 7.4 | A2L records are conserved *additions* keyed by `job_id` / `attempt_id`; they never duplicate Job/Attempt state. Read models join at query time (join-not-copy). | Unconditional |

### Violations
| Violation | Details |
|---|---|
| Copying job/attempt status, timestamps or payloads into A2L tables | Violates invariant 7.4 (join-not-copy). |
| Configuring or hardcoding a standalone A2L service URL | Violates invariant 7.2 — that service no longer exists. |

Before declaring a deployment production-ready, verify the checklist in
docs/admin/operations/PRODUCTION_READINESS_GATE.md.
The abbreviated list below covers the minimum:

- [ ] `JWT_SECRET` is set to a strong, unique value (not the dev default).
- [ ] `GATEWAY_ENVIRONMENT` is set to `production`.
- [ ] Model weights are pre-staged at the configured `VLLM_MODEL_PATH`.
- [ ] Container runtime (Singularity/Podman/Docker) is installed on compute nodes.
- [ ] S3/MinIO is reachable from compute nodes.
- [ ] PostgreSQL is reachable and migrations are applied.
- [ ] (Slurm sites) Direct TCP egress from compute nodes is blocked or audited.
- [ ] (K8s sites) CNI supports NetworkPolicy enforcement (Calico/Cilium).
- [ ] EgressGuard proxy port (default 8888) is reachable from containers.

---

## Related Documentation

| Document | Purpose |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Technical architecture and control-plane invariants. |
| docs/admin/security/SYSTEM_AUTHORITY_TRUST_MODEL.md | Authority model, trust boundaries, security invariants. |
| docs/admin/operations/PRODUCTION_READINESS_GATE.md | Full sign-off checklist with evidence requirements. |
| docs/admin/operations/SECRET_LIFECYCLE.md | Credential management, rotation, and revocation. |
| docs/admin/operations/DISASTER_RECOVERY.md | Backup procedures, RTO/RPO targets, restore runbooks. |
| docs/admin/operations/OPERATOR_RUNBOOK.md | Failure detection, diagnosis, and remediation. |
| docs/admin/operations/AUDIT_AND_INCIDENT_RECONSTRUCTION.md | Audit taxonomy and incident timeline reconstruction. |
| docs/admin/security/EGRESS_INFRASTRUCTURE.md | Site-level egress containment requirements. |
| [docs/architecture/boundaries/GATEWAY_SIDECAR_BOUNDARY.md](docs/architecture/boundaries/GATEWAY_SIDECAR_BOUNDARY.md) | Auth/policy enforcement boundary. |
| [docs/architecture/boundaries/WORKFLOW_TOOL_REGISTRY.md](docs/architecture/boundaries/WORKFLOW_TOOL_REGISTRY.md) | Workflow step templates and routing. |
| [TESTING.md](TESTING.md) | Test infrastructure and validation commands. |
