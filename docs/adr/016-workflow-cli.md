# ADR-016: Workflow CLI Design

**Status:** Accepted
**Date:** 2026-07-12
**Relates to:** ADR-003 (three-component gateway), ADR-005 (r4-contracts stdlib-only),
ADR-012 (Assistant Service), ADR-002 (durable job/attempt separation),
mutation-safety invariant (Gateway proxy/policy only), development.instructions.md

## Context

UMRS owns workflow definition, registration, persistence, and execution.  Users
need a **command-line interface** to interact with UMRS workflows from their
workstation without writing code.  The `r4` CLI already serves as the primary
user-facing tool for platform operations (auth, models, endpoints, projects,
chat, mcp).  Workflow management is the missing `r4` subcommand.

### Current State

The `r4 workflow` CLI already exists with five subcommands:

| Subcommand | Function | Gateway Route |
|------------|----------|---------------|
| `r4 workflow list` | List registered workflows | `GET /gw/workflows` |
| `r4 workflow trigger <name>` | Submit a workflow run | `POST /gw/workflows/trigger` |
| `r4 workflow upload <path>` | Register a workflow bundle | `POST /gw/workflows/upload` |
| `r4 workflow status <id>` | Check execution status | `GET /gw/workflows/<id>` |
| `r4 workflow cancel <id>` | Cancel a running execution | `POST /gw/workflows/<id>/cancel` |

Implementation lives in:
- `r4/src/r4/workflows.py` — `WorkflowClient` (HTTP transport layer)
- `r4/src/r4/_cli/workflow.py` — `WorkflowCommand` (argparse CLI handler)
- `r4-contracts/src/r4_contracts/workflows.py` — DTOs
  (`WorkflowIR`, `WorkflowStatus`, `WorkflowResult`, `WorkflowUploadResult`)

Limitations of the current CLI:
- **No `logs` subcommand** — users cannot inspect execution output from the CLI
- **No `draft` subcommand** — workflows must be uploaded as ZIP bundles; no
  YAML/JSON draft path for declarative workflow authoring
- **No batch operations** — only single workflow submit/status
- **No parameter validation before submission** — errors surface at UMRS
  execution time, not at the CLI layer

### Why a Workflow CLI Matters

1. **Operator workflow** — HPC users submit research pipelines via `r4 workflow
   trigger` from login nodes or their laptops.  They need the full lifecycle
   visible in the terminal.
2. **Companion integration** — the R4 Companion VS Code extension delegates
   workflow operations to the `r4` CLI (same path as `r4 chat`, `r4 project`).
3. **Demo and teaching** — all demos (`05_mcp_workflows/`, `06_complex_workflow/`)
   reference `r4 workflow` as the primary interface.
4. **Platform parity** — without the CLI, workflow management only exists as
   programmatic SDK usage, excluding non-programmers.

## Decision

### 1. Extend the Existing `r4 workflow` Subcommand Set

Add three new subcommands while preserving all existing ones:

```
r4 workflow list            — list registered workflows
r4 workflow run <name>      — submit a workflow run (rename of `trigger`)
r4 workflow status <id>     — check execution status
r4 workflow cancel <id>     — cancel a running execution
r4 workflow logs <id>       — stream/fetch execution logs
r4 workflow draft           — author workflows declaratively (YAML/JSON)
r4 workflow upload <path>   — register a workflow bundle (legacy path)
```

**`trigger` → `run`:** The existing `trigger` subcommand is renamed to `run` for
clarity.  `trigger` is aliased as a deprecated synonym with a deprecation
warning.  Users mean "run this workflow," not "trigger an event."  The rename
aligns with UMRS terminology where workflow executions are "runs."

### 2. Gateway Proxy, Not Direct UMRS Access

All workflow CLI commands route through the Gateway:

```
r4 CLI → Gateway HTTP → UMRS workflow service
```

This follows the platform's core boundary invariant (mutation-safety):
- Gateway handles authentication, rate limiting, and audit logging
- CLI never speaks ZMQ or connects directly to UMRS for workflow operations
- Gateway routes: `/gw/workflows/*`

The `WorkflowClient` class already implements this pattern.  No new transport
path is introduced.

### 3. Authentication via r4 Client JWT/session

The `r4` CLI already authenticates via JWT tokens stored in the r4 auth
profile (`r4 auth login`).  Workflow commands reuse this same credential
through `Client._transport_router`, which attaches the `Authorization: Bearer
<JWT>` header to all Gateway requests.

For long-running operations (`run --wait`, `logs --follow`), JWT expiry is
handled by the existing refresh flow in `r4/client/auth.py`.

### 4. New Subcommand Specifications

#### `r4 workflow logs <id>`

Retrieve execution logs for a specific workflow run.  Supports two modes:

```bash
# Fetch all logs at once
r4 workflow logs <execution_id>

# Stream logs (tail mode)
r4 workflow logs <execution_id> --follow --tail 50

# JSON output for tool integration
r4 workflow logs <execution_id> --json
```

**Implementation:**
- HTTP `GET /gw/workflows/<id>/logs` — fetches stored logs from UMRS
- `--follow` uses Server-Sent Events (SSE) if UMRS supports streaming, falls
  back to polling at 2s intervals if not
- `--tail N` limits output to last N lines
- Log format is plain text with optional JSON structured fields

**Error handling:**
- 404 → "Execution <id> not found or logs not available"
- 403 → "No permission to view logs for this execution"
- Gateway timeout → "Timed out waiting for logs. Try again."

#### `r4 workflow draft [path]`

Author workflows declaratively using YAML (preferred) or JSON.  The draft
command has three modes:

```bash
# Interactive draft creation (opens editor)
r4 workflow draft

# Draft from existing file
r4 workflow draft ./my-workflow.yaml

# Validate draft without uploading
r4 workflow draft ./my-workflow.yaml --dry-run

# Draft then upload and trigger in one step
r4 workflow draft ./my-workflow.yaml --upload --trigger --param key=value
```

**YAML format:**

```yaml
# my-workflow.yaml
name: ct-segmentation-pipeline
description: "Segment CT scans using trained model"
version: "1.0"
tags: [ct-analysis, segmentation]

parameters:          # user-provided at run time
  scan_path:
    type: string
    required: true
    description: "Path to CT scan file"
  threshold:
    type: float
    default: 0.5
    description: "Segmentation threshold"

steps:               # DAG definition
  - name: preprocess
    image: r4-registry/preprocess:v2
    command: ["python", "preprocess.py", "--input", "${params.scan_path}", "--threshold", "${params.threshold}"]
    resources:
      gpu: 1
      cpu: 4
      memory_mb: 8192
    artifacts:
      outputs: [preprocessed.nrr]

  - name: segment
    image: r4-registry/segment:v3
    command: ["python", "segment.py", "--input", "{{steps.preprocess.outputs.preprocessed.nrr}}", "--model", "${params.model}"]
    resources:
      gpu: 1
      cpu: 8
      memory_mb: 16384
    depends_on: [preprocess]
    artifacts:
      inputs: [preprocessed.nrr]
      outputs: [segmentation.nrr]

  - name: report
    image: r4-registry/report:v1
    command: ["python", "report.py", "--segmentation", "{{steps.segment.outputs.segmentation.nrr}}", "--output", "report.json"]
    depends_on: [segment]
    resources:
      cpu: 2
      memory_mb: 4096
    artifacts:
      inputs: [segmentation.nrr]
      outputs: [report.json]

egress: none         # or: egress_allowed_domains: [huggingface.co]
```

**Parameter interpolation:**
- `${params.<name>}` — parameter substitution at trigger time
- `{{steps.<name>.outputs.<artifact>}}` — artifact path resolution between steps
- `${env.<key>}` — environment variable injection

**Validation (pre-flight, before UMRS submission):**
- YAML/JSON syntax check
- Step name uniqueness
- DAG cycle detection (topological sort)
- Resource specification syntax (gpu/cpu/memory must be positive integers)
- Image reference format validation
- Parameter reference completeness (all `${params.*}` have declarations)

#### `r4 workflow batch run`

Submit multiple workflow runs in a single command:

```bash
# From a YAML file listing runs
r4 workflow batch run ./runs.yaml

# From stdin
echo '{"workflow": "ct-analysis", "params": {"scan": "/data/ct001.nrr"}}' | r4 workflow batch run -

# JSON output
r4 workflow batch run ./runs.yaml --json
```

**Batch input format (`runs.yaml`):**

```yaml
runs:
  - workflow: ct-analysis
    params:
      scan: /data/ct001.nrr
    wait: false

  - workflow: ct-analysis
    params:
      scan: /data/ct002.nrr
    wait: false

  - workflow: video-analysis
    params:
      video: /data/video001.mp4
    wait: true
```

**Batch behavior:**
- Submits all runs in parallel (default concurrency: 5)
- Reports results in submission order
- Continues on individual failures (unless `--fail-fast`)
- `wait: true` on a run blocks the batch until that run completes
- `--max-concurrency N` controls parallelism (prevents overwhelming UMRS)
- `--fail-fast` stops on first failure

### 5. Error Handling Patterns

All workflow commands use consistent error formatting:

```bash
# Not found
r4 workflow status nonexistent-id
# Error: Execution 'nonexistent-id' not found

# Permission denied
r4 workflow status other-user-id
# Error: Permission denied. You do not have access to this execution.

# Timeout
r4 workflow run my-workflow --wait
# Error: Timed out after 300s. Check status: r4 workflow status <id>

# Validation error (draft)
r4 workflow draft ./broken.yaml
# Error: Draft validation failed:
#   step.preprocess: gpu must be a positive integer, got "none"
#   step.segment: depends_on references undefined step "pre-process"

# Authentication error
r4 workflow list
# Error: Authentication required. Run: r4 auth login
```

Error codes:
| Exit Code | Meaning |
|-----------|---------|
| 0 | Success |
| 1 | Unexpected error (exception) |
| 2 | User input error (validation, bad params) |
| 3 | Authentication/authorization failure |
| 4 | Gateway timeout |

### 6. Relationship to Existing Architecture

#### Gateway Integration

The CLI routes through the Gateway, which is the **only** path for external
clients (mutation-safety invariant).  The Gateway's workflow routes proxy to
UMRS:

```
r4 workflow CLI
    → Gateway: /gw/workflows/*
        → UMRS workflow service (HTTP, internal)
```

The Gateway handles:
- JWT verification and user identity propagation
- Rate limiting on workflow operations
- Audit logging of all workflow actions
- Response serialization to `r4-contracts` DTOs

#### r4-contracts DTOs

All workflow data shapes live in `r4-contracts/src/r4_contracts/workflows.py`,
following ADR-005 (stdlib-only, `@dataclass`):

| DTO | Purpose |
|-----|---------|
| `WorkflowIR` | Workflow intermediate representation (DAG definition) |
| `WorkflowStatus` | Execution status with step states |
| `WorkflowResult` | Final result with artifacts and exit code |
| `WorkflowUploadResult` | Response from workflow upload |
| `WorkflowCatalogItem` | Registered workflow metadata (name, description, tags) |

The draft YAML format is **converted to `WorkflowIR`** by the CLI before
submission.  This keeps the contract stable regardless of how workflows are
authored.

#### Durable Job Semantics

Workflow runs inherit UMRS durable job semantics (ADR-002):
- Each run is a Job with Attempt tracking
- Crash recovery requeues from the last successful attempt
- Output isolation per attempt (`s3://.../jobs/<run_id>/attempt_<n>/...`)
- Commit pointer marks the winning attempt

The CLI surfaces this through status output:

```bash
r4 workflow status <id>
# execution_id : run-abc123
# status       : COMPLETED
# attempt      : 1/1
# started      : 2026-07-12T10:30:00Z
# finished     : 2026-07-12T10:35:22Z
# steps:
#   preprocess : COMPLETED (42s)
#   segment    : COMPLETED (140s)
#   report     : COMPLETED (22s)
```

## Consequences

### Positive

1. **Complete workflow lifecycle in CLI** — users can draft, upload, trigger,
   monitor, cancel, and inspect logs without leaving the terminal.
2. **Declarative workflow authoring** — YAML draft format is version-controllable,
   shareable, and reviewable (unlike ZIP bundles).
3. **Batch operations** — researchers running parameter sweeps or dataset
   processing pipelines can submit many runs without scripting.
4. **Consistent error patterns** — predictable error messages and exit codes
   enable automation and scripting.
5. **Follows existing architecture** — Gateway proxy, r4-contracts DTOs, JWT
   auth, durable job semantics. No new transport or stateful component.

### Negative / Trade-offs

1. **`trigger` → `run` rename** — existing scripts using `r4 workflow trigger`
   must be updated.  Mitigation: `trigger` remains as a deprecated alias with
   a warning banner for 90 days.
2. **Draft format lock-in risk** — the YAML draft format becomes a de-facto
   standard.  Future changes must maintain backward compatibility.  The format
   is opinionated (requires `steps`, `resources`, `dag`) — users wanting
   unstructured n8n JSON must still use `upload`.
3. **Batch concurrency controls needed** — unbounded parallelism could overwhelm
   UMRS.  Default cap of 5 concurrent runs; `--max-concurrency` allows tuning.
4. **Log streaming adds dependency** — `--follow` mode needs either SSE support
   in Gateway/UMRS or a polling implementation.  Polling at 2s intervals is the
   fallback but is less efficient.

### Unknowns

- **Log retention:** How long does UMRS retain workflow logs?  Does the CLI need
  pagination for long-running workflows?
- **Draft schema versioning:** If the YAML format changes, how do we handle
  legacy drafts?  Consider a `schema_version` field.
- **Multi-cluster workflow routing:** On `umrs-front` dual-cluster deployments,
  which UMRS cluster handles workflow execution?  Currently resolved via the
  Gateway's affinity routing (ADR-012 path).

## Alternatives Considered

- **n8n-native CLI** — Use n8n's own CLI for workflow management.  Rejected: the
  platform wraps n8n as an implementation detail; users should not depend on
  n8n's internal interfaces.  The `r4 workflow` CLI abstracts n8n away.
- **REST-only (no CLI)** — Rely on programmatic SDK usage.  Rejected: operator
  workflows are inherently interactive; a CLI is the expected interface for
  HPC users and researchers.
- **Direct UMRS ZMQ** — Have the CLI speak ZMQ directly to UMRS.  Rejected:
  violates the Gateway-boundary invariant.  CLI must always go through the
  Gateway, which provides auth, audit, and rate limiting.

## CLI Command Reference

### `r4 workflow list`

```
r4 workflow list [--scope user|project|system] [--name <filter>] [--json]
```

List registered workflows.  Default scope is `user` (only workflows owned by or
shared with the current user).

### `r4 workflow run <name>`

```
r4 workflow run <name> \
  --param KEY=VALUE \
  --input-file <path> \
  [--project <id>] \
  [--mcp-tool <tool>] \
  [--wait] \
  [--json]
```

Submit a workflow run.  `--wait` blocks until completion (polls every 5s).
`--param` and `--input-file` are repeatable.

### `r4 workflow status <id>`

```
r4 workflow status <id> [--json]
```

Show execution status with step-level detail.

### `r4 workflow cancel <id>`

```
r4 workflow cancel <id>
```

Cancel a running or pending execution.  Irreversible.

### `r4 workflow logs <id>`

```
r4 workflow logs <id> [--follow] [--tail N] [--json]
```

Fetch or stream execution logs.

### `r4 workflow draft [path]`

```
r4 workflow draft [path] [--dry-run] [--upload] [--trigger] [--param KEY=VALUE]
```

Create or validate a YAML/JSON workflow draft.  `--dry-run` validates without
uploading.  `--upload` registers the draft with UMRS.  `--trigger` submits a run
after upload.

### `r4 workflow upload <path>`

```
r4 workflow upload <path> [--name <override>] [--json]
```

Upload a workflow ZIP bundle or directory.  Legacy path for n8n-native JSON
workflows.

### `r4 workflow batch run`

```
r4 workflow batch run <file> [--max-concurrency N] [--fail-fast] [--json]
```

Submit multiple workflow runs from a YAML file.
