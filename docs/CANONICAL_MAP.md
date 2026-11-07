# Canonical Topic Map

> **Purpose:** Machine-readable map from every major topic to its canonical document.
> Use it to look a topic up; the reading order lives in [README.md](README.md)
> (the doc map; until 2026-10-18 this line said "consult this file first").
> **Last updated:** 2026-10-18 (pointer to the doc map; gap-row range; agent entry files)

## How to use this map

1. Find your topic in the tables below.
2. Read the canonical document. For anything about *what the platform should be*, the canonical document is the design; for *what the code does*, it is the gap analysis or the overview; for *why*, an ADR.
3. Documents dated before 2026-09-22 are implementation references as of their date; verify against code before citing.

## Design and status

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| Target design (vision, objectives, pillars, catalog, loop, topology, security) | [R4-UMRS-DESIGN.md](R4-UMRS-DESIGN.md) | Changes are ADRs |
| Design vs implementation, per requirement | [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](R4-UMRS-GAP-ANALYSIS-2026-09-22.md) | Rows `D-01`…`D-64` with evidence paths (range corrected 2026-10-18) |
| What runs today, code map, deployments, baselines, queue | [R4-UMRS-PLATFORM-OVERVIEW.md](R4-UMRS-PLATFORM-OVERVIEW.md) | Dated header |
| Planning: order of work, objectives, release track and escalations, wave ledger | Kept by the maintainers in the development repository: ../ROADMAP.md, ../DEVELOPMENT_OBJECTIVES.md, ../STATE.md, development/wave-ledger.md | Not in the public repository (rows merged 2026-11-07) |

## Architecture

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| Target architecture + implementation map | [../ARCHITECTURE.md](../ARCHITECTURE.md) | Two layers kept apart |
| Component catalog (pre-design detail) | [architecture/design/components.md](architecture/design/components.md) | 2026-06 |
| Security model (pre-design detail) | [architecture/design/security.md](architecture/design/security.md) | Enforced invariants: [../CONSTITUTION.md §2](../CONSTITUTION.md) |
| Data flow diagrams (pre-design detail) | [architecture/design/dataflow.md](architecture/design/dataflow.md) | |
| API design and route tables | [architecture/design/apis.md](architecture/design/apis.md) | HTTP families; ZeroMQ contract in `umrs/doc/zmq_api_contract.md` |
| Glossary | [architecture/design/glossary.md](architecture/design/glossary.md) | Rewritten 2026-09-22 |

## Runtime

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| UMRS core runtime spec | [architecture/runtime/UMRS_CORE_RUNTIME_SPEC.md](architecture/runtime/UMRS_CORE_RUNTIME_SPEC.md) | Ownership, APIs, invariants |
| Runtime topology (boot sequence, threads) | [architecture/runtime/RUNTIME_TOPOLOGY_REFERENCE.md](architecture/runtime/RUNTIME_TOPOLOGY_REFERENCE.md) | |
| Transports (ZeroMQ + HTTP) | [architecture/runtime/COMMUNICATION_ARCHITECTURE.md](architecture/runtime/COMMUNICATION_ARCHITECTURE.md) | Dual transport is the design since ADR-059 (D-01 Implemented by design amendment; corrected 2026-10-18) |
| Failure injection / recovery paths | [architecture/runtime/RUNTIME_FAILURE_INJECTION_REVIEW.md](architecture/runtime/RUNTIME_FAILURE_INJECTION_REVIEW.md) | |
| Determinism gaps | [architecture/runtime/OPERATIONAL_DETERMINISM_GAPS.md](architecture/runtime/OPERATIONAL_DETERMINISM_GAPS.md) | |

## Boundaries

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| R4 client role | [architecture/boundaries/R4_CLIENT_ROLE_DEFINITION.md](architecture/boundaries/R4_CLIENT_ROLE_DEFINITION.md) | |
| Integration policy (UMRS-first) | [architecture/boundaries/INTEGRATION_POLICY.md](architecture/boundaries/INTEGRATION_POLICY.md) | |
| Package layout and install topology | [architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md](architecture/boundaries/LIBRARY_GROUP_STRUCTURE.md) | |
| Edge ↔ UMRS sidecar contract | [architecture/boundaries/GATEWAY_SIDECAR_BOUNDARY.md](architecture/boundaries/GATEWAY_SIDECAR_BOUNDARY.md) | The edge is UMRS's edge process (ADR-059; D-02 Implemented by design amendment; corrected 2026-10-18) |
| Workflow step routing / tool registry | [architecture/boundaries/STEP_ROUTING.md](architecture/boundaries/STEP_ROUTING.md), [WORKFLOW_TOOL_REGISTRY.md](architecture/boundaries/WORKFLOW_TOOL_REGISTRY.md) | |
| UMRS submodule boundary | [../umrs/doc/AI4S_PLATFORM_BOUNDARY.md](../umrs/doc/AI4S_PLATFORM_BOUNDARY.md) | Branch policy ADR-010 |

## Governance

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| Non-negotiable rules | [../CONSTITUTION.md](../CONSTITUTION.md) | |
| Doc map (reading order, truth homes, status vocabulary) | [README.md](README.md) | Since 2026-10-18 |
| Agent entry (every vendor) | [../AGENTS.md](../AGENTS.md) | Tracked since 2026-10-18; [../CLAUDE.md](../CLAUDE.md), [../GEMINI.md](../GEMINI.md), [../.github/copilot-instructions.md](../.github/copilot-instructions.md) are pointers |
| Onboarding (developer, agent) | [onboarding/developer.md](onboarding/developer.md), [onboarding/agent.md](onboarding/agent.md) | Since 2026-10-18 |
| R4 product-agent briefing | [../R4.md](../R4.md) | Loaded by `r4/src/r4/briefing.py` |
| Domain rules for agents | [../.github/instructions/](../.github/instructions/) | `development`, `deployment-operations`, `living-documentation`, `ci-harness`, `codacy` (gitignored, local only; M-H6 open, 2026-10-18), `companion`, `mutation-safety`, `agent-safety`, `umrs-branch-separation` |
| Change governance tiers | [architecture/governance/STEADY_STATE_ENGINEERING_POLICY.md](architecture/governance/STEADY_STATE_ENGINEERING_POLICY.md) | Pre-reset (2026-05); retirement pending the constitution amendment (plan H-11.3, noted 2026-10-18) |
| Validators and enforcement | [architecture/governance/ARCHITECTURE_ENFORCEMENT.md](architecture/governance/ARCHITECTURE_ENFORCEMENT.md) | `tools/`, `scripts/ci-lint-local.sh` |
| Complexity, cleanup, compatibility, validator promotion | [architecture/governance/](architecture/governance/) | |
| Living documentation | [../.github/instructions/living-documentation.instructions.md](../.github/instructions/living-documentation.instructions.md) | |

## Administration

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| Admin guide | admin/admin-guide.md | |
| Deployment (Ansible only) | ../.github/instructions/deployment-operations.instructions.md, admin/deployment/ | Profiles: umrs-front, rccs-cloud, bdrt, octopus, atom-k8s |
| Operations runbooks | admin/operations/ | |
| Security (admin) | admin/security/ | |
| Operator guarantees | [../PLATFORM_CONTRACT.md](../PLATFORM_CONTRACT.md) | Current implementation |

## User documentation

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| Introduction | [getting-started/introduction.md](getting-started/introduction.md) | Rewritten 2026-09-22 |
| User guide | [getting-started/user-guide.md](getting-started/user-guide.md) | |
| Troubleshooting | [getting-started/troubleshooting.md](getting-started/troubleshooting.md) | |
| Full manual | [user-manual/README.md](user-manual/README.md) | 28 chapters |
| Knowledge, catalog planes, autonomous research | [user-manual/27-knowledge-management.md](user-manual/27-knowledge-management.md), [28-knowledge-catalogue-visibility.md](user-manual/28-knowledge-catalogue-visibility.md), [26-autonomous-research.md](user-manual/26-autonomous-research.md) | "catalogue" there = data catalog, not the Scientific Catalog (gap D-39) |
| R4 docs (quickstart, console, CLI, SDK) | [r4/index.md](r4/index.md) | |

## Development planning

Development plans are kept privately by the maintainers in the development repository (index: development/README.md); they are not in the public repository (tables folded 2026-11-07).

## Decision records

| Topic | Canonical document | Notes |
|-------|-------------------|-------|
| ADR index | [adr/README.md](adr/README.md) | Rollout blocks are current truth |

## Package READMEs

| Package | README | Description |
|---------|--------|-------------|
| `r4/` | [../r4/README.md](../r4/README.md) | R4 client: console, CLI, SDK, companion |
| `umrs/` | [../umrs/Readme.md](../umrs/Readme.md) | UMRS server (submodule) |
| `gateway/` | [../gateway/README.md](../gateway/README.md) | NGINX + auth sidecar edge |
| `mcp-servers/` | [../mcp-servers/README.md](../mcp-servers/README.md) | Platform MCP servers + default Hatch registry |
| `r4-contracts/` | [../r4-contracts/README.md](../r4-contracts/README.md) | Stdlib-only DTOs |
| `packages/r4-tui-v3/` | [../packages/r4-tui-v3/README.md](../packages/r4-tui-v3/README.md) | The console |

## Historical archive

Pre-design-reset and superseded documents are kept privately by the maintainers under history/; they are not in the public repository (table folded 2026-11-07).
