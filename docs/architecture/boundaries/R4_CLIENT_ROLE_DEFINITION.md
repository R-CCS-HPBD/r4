# R4 Client Role Definition

*Established: 2026-05-13 — Final Convergence Phase*
*Status: CANONICAL — Permanent Reference*

## Purpose

This document defines R4 as an **optional upper-layer client and orchestration
consumer** of the UMRS runtime platform. R4 is replaceable; UMRS is authoritative.

---

## 1. R4 Role Classification

R4 is:

| Role | Description |
|------|-------------|
| **CLI** | Command-line interface for users interacting with UMRS services |
| **SDK** | Python library wrapping UMRS APIs for programmatic access |
| **Orchestration UX** | Workflow authoring, validation, and publishing tools |
| **Agent Tooling Layer** | Companion, research assistant, MCP tool management |
| **Recipes/Skills Authoring** | Declarative task definitions and composable automation |
| **Transport Consumer** | Uses ZMQ and HTTP protocols defined by UMRS |
| **Optional Runtime Client** | UMRS operates with or without R4 connected |

---

## 2. What R4 Owns

| Domain | Scope | Notes |
|--------|-------|-------|
| CLI commands and UX | All `r4 *` commands | User-facing interaction |
| SDK client wrappers | `r4.Client`, transport helpers | DTO/request wrappers over UMRS APIs |
| Workflow authoring | Templates, init, validate, save, publish | Local-only; runtime is UMRS |
| Companion/chat | Interactive assistant, coding tools | Agent logic; inference via UMRS |
| TUI/interactive UI | Terminal UI for real-time interaction | Presentation layer |
| Jupyter integration | Magic commands, notebook helpers | Convenience layer |
| MCP tool registration UX | `r4 mcp register/list` | UMRS owns runtime; R4 owns CLI UX |
| Local workflow catalog | Declarative pointers to workflows | No runtime state |
| Skill/recipe definitions | `r4-contracts` DTO definitions | Shared contract types |

---

## 3. What R4 MUST NOT Own

| Forbidden Domain | Reason | Correct Owner |
|-----------------|--------|---------------|
| Job scheduling or dispatch | Runtime authority is UMRS-exclusive | UMRS Scheduler |
| Retry logic or backoff | Durable job semantics belong to UMRS | UMRS JobStore |
| Attempt state transitions | Persistence authority is UMRS-exclusive | UMRS JobStore |
| Reconciliation / crash recovery | Boot-time recovery is UMRS-exclusive | UMRS Reconciler |
| Backend orchestration | Container lifecycle is UMRS-exclusive | UMRS backends |
| Model registry state | Model metadata/ACL is UMRS-exclusive | UMRS Model Registry |
| Endpoint readiness polling | Provision monitoring is UMRS-exclusive | UMRS Monitor |
| Transport protocol definition | ZMQ/HTTP protocols are UMRS-defined | UMRS MessageQueue |
| Durable state storage | Postgres schemas are UMRS-exclusive | UMRS persistence |
| S3 credential management | Object storage is UMRS-managed | UMRS provisioning |
| Workflow execution state | Run state is UMRS durable job | UMRS workflow runtime |

---

## 4. Allowed Dependency Directions

```
┌─────────────────────────────────────────────────────┐
│                   Dependency Graph                   │
│                                                     │
│   r4-contracts ←─── r4 ───→ UMRS (via wire only)   │
│        │                         ↑                  │
│        └─────── Gateway ─────────┘                  │
│                 (via HTTP only)                      │
└─────────────────────────────────────────────────────┘
```

| Direction | Allowed | Mechanism |
|-----------|---------|-----------|
| R4 → UMRS | Yes | ZMQ wire protocol, HTTP API |
| R4 → r4-contracts | Yes | Direct Python import |
| Gateway → UMRS | Yes | HTTP to sidecar |
| Gateway → r4-contracts | Yes | Direct Python import |
| UMRS → R4 | **FORBIDDEN** | N/A |
| UMRS → Gateway | **FORBIDDEN** | N/A |
| R4 → Gateway | **FORBIDDEN** | N/A |

**Enforced by**: `import_boundary_validator.py` (hard-fail in CI)

---

## 5. Supported Integration Patterns

### Pattern A: ZMQ Direct (Full Feature Set)

```
R4 CLI/SDK → ZMQ Control Plane → UMRS Server
R4 CLI/SDK → ZMQ Data Plane → UMRS Server (file transfers)
```

- Full access to all UMRS capabilities
- Required for file transfer operations
- Used in HPC environments (SSH tunnel mode)

### Pattern B: HTTP via Gateway (REST Subset)

```
R4 CLI/SDK → Gateway HTTP → UMRS HTTP Sidecar
```

- REST-compatible subset of operations
- Used for remote access, web integrations, n8n workflows
- Gateway adds auth, rate limiting, audit

### Pattern C: HTTP Direct to Sidecar (Admin/Tooling)

```
Admin tool → UMRS HTTP Sidecar (port 8082)
```

- Direct REST access without Gateway overhead
- Used for health checks, metrics, admin operations

---

## 6. R4 Replaceability

R4 is explicitly designed to be replaceable. The following guarantees enable this:

| Guarantee | Implementation |
|-----------|---------------|
| No R4-specific state in UMRS | UMRS stores no R4 metadata |
| Wire protocol is R4-agnostic | Any ZMQ DEALER client works |
| HTTP API is R4-agnostic | Any HTTP client works |
| r4-contracts are stdlib-only | No R4/UMRS runtime dependency |
| Job specs are JSON dicts | Any client can construct them |

**Replacement scenario**: An alternative CLI/SDK (e.g., a Rust CLI, a web UI,
or a direct Python script) can operate UMRS identically by implementing the
ZMQ or HTTP protocols.

---

## 7. R4 Development Constraints

| Constraint | Rationale |
|-----------|-----------|
| No direct UMRS imports | Import boundary invariant |
| No local job stores | Job state is UMRS-exclusive |
| No workflow registries | Runtime state is UMRS-exclusive |
| No alternative state machines | UMRS owns all state transitions |
| No retry/backoff implementation | UMRS durable jobs provide this |
| DTOs in r4-contracts only | Shared types must be stdlib-only |
| `r4.Client` for all UMRS ops | No raw socket manipulation |

---

## 8. Boundary Enforcement

| Rule | Validator | Mode |
|------|-----------|------|
| No `import umrs` in r4/ | `import_boundary_validator.py` | Hard-fail |
| No `import r4` in umrs/ | `import_boundary_validator.py` | Hard-fail |
| r4-contracts zero dependencies | `contracts_fence.py` | Hard-fail |
| DTO equivalence | `dto_equivalence_check.py` | Hard-fail |
| No `import gateway` in r4/ | `import_boundary_validator.py` | Hard-fail |

---

## 9. What Happens Without R4

When R4 is absent, UMRS continues to:

- Accept ZMQ commands from any compatible client
- Serve HTTP API via sidecar
- Schedule and execute jobs
- Monitor and reconcile attempts
- Manage model registry
- Store context and memory
- Respond to Gateway requests

The only capabilities lost without R4 are:
- Convenient CLI commands
- Workflow authoring templates
- Companion/chat agent
- Jupyter integration
- TUI interactive mode
