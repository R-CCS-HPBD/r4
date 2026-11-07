# R4 + UMRS Documentation

> **Reading order:** [README.md](README.md) (the doc map) holds the
> navigation order; this page is the by-role landing and directory map.
>
> **Where to start: pick your role.** Three documents frame everything else:
> the **design** (target), the **gap analysis** (design against code) and the
> **platform overview** (what runs today). Documents dated before 2026-09-22
> describe the platform in its earlier "HPC inference platform with a Gateway"
> narrative; they are accurate about the code as of their date, not about the
> design.

---

## The three framing documents

| Document | What it answers |
|---|---|
| [R4-UMRS-DESIGN.md](R4-UMRS-DESIGN.md) | What are we building? Vision, objectives, pillars, Scientific Catalog, research loop, Hatch/Hatchling, deployment topology, security model, open questions. |
| [R4-UMRS-GAP-ANALYSIS-2026-09-22.md](R4-UMRS-GAP-ANALYSIS-2026-09-22.md) | Where is the code against that design? Rows `D-nn` with evidence paths and what closes each. It is the maintainers' tracking record. |
| [R4-UMRS-PLATFORM-OVERVIEW.md](R4-UMRS-PLATFORM-OVERVIEW.md) | What runs today, where the code lives, deployment profiles, test baselines. |

Then [../ARCHITECTURE.md](../ARCHITECTURE.md) (target and implementation map).

## New to the platform?

[Getting Started: Introduction](getting-started/introduction.md): what it is, who it is for, how a project moves through it, how to try it.

## End user (scientist)?

[User Guide](getting-started/user-guide.md): chat, projects, ingestion, endpoints, MCP tools, workflows, vision.
Also: [Quickstart](QUICKSTART.md) · [Console README](../packages/r4-tui-v3/README.md) · [Console reference](r4/TUI_REFERENCE.md) · [Knowledge management](user-manual/27-knowledge-management.md) · [Autonomous research](user-manual/26-autonomous-research.md)

## Having trouble?

[Troubleshooting](getting-started/troubleshooting.md)

## Administrator or operator?

[Administrator guide](r4/admin-guide/README.md): deployment, configuration, administration.
Also: [Deployment overview](user-manual/16-deployment.md) · [Stand-alone deployment](user-manual/29-standalone-deployment.md) · [Platform contract](../PLATFORM_CONTRACT.md)

## Security reviewer?

[SECURITY.md](../SECURITY.md) (how to report a vulnerability) · design security model: [R4-UMRS-DESIGN.md §Data flow and security model](R4-UMRS-DESIGN.md#data-flow-and-security-model) · enforced invariants: [../CONSTITUTION.md §2](../CONSTITUTION.md)

## Developer or contributor?

Start with [../CONTRIBUTING.md](../CONTRIBUTING.md) (how to contribute), [README.md](README.md) (reading order) and [onboarding/developer.md](onboarding/developer.md), then:
- [../ARCHITECTURE.md](../ARCHITECTURE.md): target architecture and where each pillar's code lives
- [Architecture: Design](architecture/design/): components, security, data flows, APIs (pre-design detail)
- [Architecture: Runtime](architecture/runtime/): UMRS runtime spec, topology, transports
- [Architecture: Boundaries](architecture/boundaries/): integration rules, package layout
- [Architecture: Governance](architecture/governance/): change policies, enforcement, validators
- [Glossary](architecture/design/glossary.md): one meaning per term

## Architecture Decision Records?

[ADRs](adr/README.md): the index lists every ADR and its status, plus amendments.

## Coding agent?

[../AGENTS.md](../AGENTS.md) (the entry for every coding agent; CLAUDE.md, GEMINI.md and the Copilot instructions point to it) · [../CONTRIBUTING.md](../CONTRIBUTING.md) (the process for this repository) · [Canonical Topic Map](CANONICAL_MAP.md) (topic to document)

---

## Directory map

| Directory | For | Content |
|-----------|-----|---------|
| `getting-started/` | New users, end users | Introduction, user guide, troubleshooting |
| `user-manual/` | End users, operators | Chapter reference and appendices |
| `r4/` | R4 users and developers | Quickstart, console reference, CLI/SDK reference, examples, administrator guide |
| `architecture/` | Developers, architects | Design detail, runtime specs, boundaries, governance, glossary |
| `adr/` | All | Architecture Decision Records |
| `onboarding/` | New developers | Developer guide; `agent.md` describes the maintainers' agent workflow |
| `benchmarks/`, `research/` | Reference | Benchmark notes, external-tool analyses |
| `tutorial/` | Learners | Tutorial labs, tasks and data |
| `autonomous-science/`, `ux/`, `guides/`, `examples/` | Reference | Earlier plans and guides; check the dated header before relying on them |
