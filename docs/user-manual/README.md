# R4 Platform — User Manual

> **Version:** August 2026  
> **Status:** Production (HPC validation ongoing)

This manual covers everything needed to use, administer, and extend the R4 platform. It is organized by audience — start with the chapter that matches your role.

---

## Table of Contents

### Part I — Getting Started

| Chapter | Audience | Description |
|---------|----------|-------------|
| [0. Account Creation & Onboarding](00-onboarding.md) | Admins + New users | Step-by-step: create an account (admin) and complete first login (user) |
| [1. Introduction](01-introduction.md) | Everyone | What R4 is, naming conventions, and platform mental model |
| [2. Quick Start](02-quickstart.md) | End users | Install the client, authenticate, and run your first commands |
| [3. Authentication](03-authentication.md) | End users | API-key, OIDC, and profile management |

### Part II — User Guide

| Chapter | Audience | Description |
|---------|----------|-------------|
| [4. Chat & Companion](04-chat.md) | End users | One-shot chat, streaming, interactive TUI, and session management |
| [5. Projects & Models](05-projects-models.md) | End users | Project lifecycle, project data classes (what may leave your machine; added 2026-10-16), model discovery, staging, and registration |
| [6. Endpoints](06-endpoints.md) | End users | Provision, inspect, and manage model endpoints (platform, public, and pools) |
| [7. RAG](07-rag.md) | End users | Document upload, embedding, search, and RAG-assisted chat |
| [8. MCP Tools](08-mcp.md) | End users | Tool discovery, registration, Hatchling packages, and custom servers |
| [9. Workflows & Jobs](09-workflows-jobs.md) | End users | Workflow upload/trigger, durable job tracking, recipes, schedules, and pipeline SDK |
| [10. Vision](10-vision.md) | End users | BentoML vision endpoints — SAM3, MAE, and custom vision models |
| [11. Multi-Source Routing](11-sources.md) | End users | Connect to multiple backends (platform, cloud, public APIs) |
| [12. Ask: Agent, Search & Teamup](12-ask.md) | End users | Agentic workflows, code search, and multi-agent collaboration |
| [26. Autonomous Research](26-autonomous-research.md) | Researchers | End-to-end: create a project, seed knowledge, and start the autonomous research loop |
| [27. Knowledge Management](27-knowledge-management.md) | End users | Unified query, multigraph, explorer TUI, `.kb` bundle export/restore with integrity + retarget |
| [28. Knowledge & Catalogue — Planes & Visibility](28-knowledge-catalogue-visibility.md) | All users | Deep design: planes, visibility families (project/system/ambient), write flows, operation modes, ontology contract |
| [24. A2L — Artifact and Agent Lineage](24-a2l.md) | End users, agent developers | Scientific provenance tracking: artifact registration, lineage graphs, and agent execution records |
| [31. Your Own HPC Accounts](31-own-hpc-accounts.md) | Researchers, site operators | Run jobs under your own HPC login and allocation: the gate, signing key and grants (gated delegation, ADR-112), what UMRS operators can and cannot do, accounting, revocation (added 2026-11-08; commands land in stages) |
| [30. Frontend-Venue Sessions](30-frontend-venue.md) | End users | Remote companion sessions on the cluster front-end: what the CPU-only worker can and cannot do, pushing a source, starting a session, web policy, audit (renumbered 2026-10-18 from a second chapter 20, which it shared with User & Access Management, and indexed) |

### Part III — CLI & SDK Reference

| Chapter | Audience | Description |
|---------|----------|-------------|
| [13. CLI Reference](13-cli-reference.md) | All users | Complete command tree (35+ commands), flags, and examples |
| [14. SDK Reference](14-sdk-reference.md) | Developers | Python API, sub-clients, and code examples |
| [15. TUI Reference](15-tui-reference.md) | End users | Interactive UI modes, slash commands, and session management |

### Part IV — Administration

| Chapter | Audience | Description |
|---------|----------|-------------|
| [16. Deployment Overview](16-deployment.md) | Operators | Deployment modes, site profiles, and prerequisites |
| [17. Docker/Compose Deployment](17-docker-deployment.md) | Operators | Local development and testing with Docker Compose |
| [18. HPC Deployment](18-hpc-deployment.md) | Operators | Slurm + Singularity on HPC clusters |
| [19. Kubernetes Deployment](19-k8s-deployment.md) | Operators | Production K8s with Helm |
| [20. User & Access Management](20-user-management.md) | Operators | User enrollment, tokens, Slurm accounts, and project membership |
| [21. UMRS Operations](21-umrs-operations.md) | Operators | UMRS server management, monitoring, and maintenance |
| [29. Stand-alone Deployment](29-standalone-deployment.md) | Operators + single users | The whole platform on one host: required conditions, bring-up, and registering it as an r4 platform source, and its projects' data class (updated 2026-10-16) |

### Part V — Demonstrations

| Chapter | Audience | Description |
|---------|----------|-------------|
| [22. Demo Suite](22-demos.md) | All users | Guided walkthrough of the full R4 demo workflow |
| [23. UMRS Demos](23-umrs-demos.md) | Operators | 32 self-contained UMRS demos covering every feature |
| 25. Site Deployment | Maintainers | The maintainers' own sites; not part of the public repository |

### Appendices

| Appendix | Description |
|----------|-------------|
| [A. Troubleshooting](appendix-a-troubleshooting.md) | Common issues and solutions |
| [B. Environment Variables](appendix-b-env-vars.md) | All configuration variables by component |
| [C. Architecture Overview](appendix-c-architecture.md) | Platform architecture, component boundaries, and data flow |
| [D. Security Model](appendix-d-security.md) | EgressGuard, isolation model, and security invariants |
| [E. Glossary](appendix-e-glossary.md) | Platform terminology and naming conventions |

---

## How to Use This Manual

- **New users:** Start with chapters 1–3, then read whichever chapters in Part II match your workflow.
- **Developers integrating via SDK:** Jump to chapters 2, 14, and the examples directory.
- **Administrators deploying the platform:** Start with chapters 16–21.
- **Running demos:** Chapter 22 for the R4 demo suite; chapter 23 for detailed UMRS demos.
- **Building custom workflows:** See the [Custom Workflow Guide](../guides/custom-workflows.md) for Dockerfile-in-workflow, pre-built images, GPU support, and auto-trigger.

## Related Resources

- [R4 Docs Index](../r4/index.md) — Quick-reference docs hub (lightweight)
- [Platform Contract](../../PLATFORM_CONTRACT.md) — Operator/user guarantees
- Development Objectives — Roadmap and status
- [Contributing](../../CONTRIBUTING.md) — Developer onboarding
- [Anti-Roadmap](../r4/ANTI_ROADMAP.md) — What R4 will NOT build
