# Architecture Documentation

> **Updated 2026-09-22 (design reset)**
> **Audience:** Developers, architects, contributors, coding agents
> **Status:** index of the architecture documents. The design is the
> target; the gap analysis numbers every difference to the code; the
> platform overview says what runs today. Documents dated before
> 2026-09-22 describe the implementation as of their date — verify against
> the code before citing them.

## Start here

| Document | What it is |
|---|---|
| [R4-UMRS-DESIGN.md](../R4-UMRS-DESIGN.md) | The adopted design: four pillars (R4, UMRS, Scientific Catalog, custom MCP servers), the research loop, deployment topology, the security model |
| [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md) | Design vs code, rows D-01 … D-56 with file paths; cited everywhere as `D-nn` |
| [R4-UMRS-PLATFORM-OVERVIEW.md](../R4-UMRS-PLATFORM-OVERVIEW.md) | What runs today: elements, deployments, tests, queue |
| [ARCHITECTURE.md](../../ARCHITECTURE.md) | Short two-layer reference (target + implementation map) and the invariants enforced today |
| [CONSTITUTION.md](../../CONSTITUTION.md) | Non-negotiable rules, validators, wave contract |
| [ADR index](../adr/README.md) | Decisions; rollout blocks inside ADRs are current truth |

Then read [design/architecture.md](design/architecture.md) for the
per-pillar detail.

> **Coding agents:** [Canonical Topic Map](../CANONICAL_MAP.md) has the
> machine-readable topic → document mapping.

## Subdirectories

### [Design](design/) — rewritten 2026-09-22

Design-level references: each states the target from the design, then what
the tree does today, with gap rows where they differ.

| Document | Description |
|----------|-------------|
| [architecture.md](design/architecture.md) | Target architecture (pillars, security statement, loop, topology) and the implementation map per pillar; key decisions with ADRs |
| [components.md](design/components.md) | Component catalog by pillar (R4, UMRS, Scientific Catalog parts, custom MCP), the edge today, shared contracts; verified paths and status |
| [security.md](design/security.md) | The design's security table and, per layer, what enforces it today with file paths and gap rows |
| [dataflow.md](design/dataflow.md) | The design's target sequence, where today differs, and today's flows (chat, ingestion, files, provisioning, remote sessions, MCP, jobs, auth, loop) |
| [apis.md](design/apis.md) | Edge route table, the 42 UMRS families by lane, verified routes, the ZeroMQ string-command contract, bridge v1, the versioned-protocol target (D-04) |
| [glossary.md](design/glossary.md) | Canonical terms from the design with their implementation counterparts |

### [Runtime](runtime/) — pre-reset (2026-05 … 2026-08)

Implementation-level runtime specifications of UMRS; still accurate on
process structure, dated on transport framing.

| Document | Description |
|----------|-------------|
| [UMRS_CORE_RUNTIME_SPEC.md](runtime/UMRS_CORE_RUNTIME_SPEC.md) | UMRS as a standalone runtime: ownership boundaries, APIs, invariants |
| [RUNTIME_TOPOLOGY_REFERENCE.md](runtime/RUNTIME_TOPOLOGY_REFERENCE.md) | Boot sequence, thread map, component authority |
| [RUNTIME_FAILURE_INJECTION_REVIEW.md](runtime/RUNTIME_FAILURE_INJECTION_REVIEW.md) | Failure modes and recovery paths |
| [COMMUNICATION_ARCHITECTURE.md](runtime/COMMUNICATION_ARCHITECTURE.md) | Every communication path and the transport chosen for it (dual transport as built; the design's single transport is D-01) |
| [TRANSPORT_PARITY_MATRIX.md](runtime/TRANSPORT_PARITY_MATRIX.md) | HTTP vs ZeroMQ parity per operation (draft) |
| [OPERATIONAL_DETERMINISM_GAPS.md](runtime/OPERATIONAL_DETERMINISM_GAPS.md) | Environment coupling, race conditions, timing risks |

### [Boundaries](boundaries/) — pre-reset

Integration rules and package layout; the ownership table in
[CONSTITUTION.md §1.3](../../CONSTITUTION.md) is the current statement.

| Document | Description |
|----------|-------------|
| [umrs_r4_boundary.md](umrs_r4_boundary.md) | UMRS owns scientific knowledge, R4 orchestrates research: the RC1 boundary principle |
| [R4_CLIENT_ROLE_DEFINITION.md](boundaries/R4_CLIENT_ROLE_DEFINITION.md) | R4 as the replaceable upper-layer client of UMRS |
| [INTEGRATION_POLICY.md](boundaries/INTEGRATION_POLICY.md) | UMRS-first integration boundaries |
| [LIBRARY_GROUP_STRUCTURE.md](boundaries/LIBRARY_GROUP_STRUCTURE.md) | Package boundary and install topology |
| [GATEWAY_SIDECAR_BOUNDARY.md](boundaries/GATEWAY_SIDECAR_BOUNDARY.md) | Edge ↔ UMRS sidecar contract (Python gateway rows are retired, ADR-014) |
| [STEP_ROUTING.md](boundaries/STEP_ROUTING.md) | Workflow step routing between the runner and `Step.execute()` |
| [WORKFLOW_TOOL_REGISTRY.md](boundaries/WORKFLOW_TOOL_REGISTRY.md) | Built-in pipeline step templates |

### [Governance](governance/) — pre-reset

Change policies, enforcement and validator reference. The wave contract
and the validator scorecard live in [CONSTITUTION.md §7–§9](../../CONSTITUTION.md).

| Document | Description |
|----------|-------------|
| [ARCHITECTURE_STEADY_STATE_GUIDE.md](governance/ARCHITECTURE_STEADY_STATE_GUIDE.md) | Stable / transitional / forbidden classification |
| [STEADY_STATE_ENGINEERING_POLICY.md](governance/STEADY_STATE_ENGINEERING_POLICY.md) | Acceptable and forbidden change categories |
| [COMPATIBILITY_LIFECYCLE_POLICY.md](governance/COMPATIBILITY_LIFECYCLE_POLICY.md) | Component retirement governance |
| [ARCHITECTURE_GOVERNANCE.md](governance/ARCHITECTURE_GOVERNANCE.md) | Review triggers, ADR lifecycle |
| [ARCHITECTURE_ENFORCEMENT.md](governance/ARCHITECTURE_ENFORCEMENT.md) | Validator reference and failure diagnostics |
| [COMPLEXITY_POLICY.md](governance/COMPLEXITY_POLICY.md) | Complexity management rules |
| [CLEANUP_EXECUTION_PLAN.md](governance/CLEANUP_EXECUTION_PLAN.md) | Module lifecycle: planned-inactive, orphans |
| [VALIDATOR_STABILITY_REVIEW.md](governance/VALIDATOR_STABILITY_REVIEW.md) | Validator fragility assessment |
| [PRE_PUSH_VALIDATION.md](governance/PRE_PUSH_VALIDATION.md) | Pre-push validation checklist |

## Archived documents

Completed phase docs, milestone plans and superseded architecture
references live under `docs/history/architecture/`;
the pre-reset top-level documents are under
`docs/history/design-reset-2026-09-22/`.
They carry dated banners and are never cited as current.
