# Documentation map: R4 and UMRS

This page is the reading order for the public R4 repository. Start with the
[top-level README](../README.md) for what R4 is and how to install it.

## 1. Reading order

| # | Read | Why |
|---|---|---|
| 1 | Humans: [CONTRIBUTING.md](../CONTRIBUTING.md), then [onboarding/developer.md](onboarding/developer.md). Agents: [AGENTS.md](../AGENTS.md) | Setup, the tests and lint that must pass, how changes are reviewed |
| 2 | [R4-UMRS-DESIGN.md](R4-UMRS-DESIGN.md) | The target architecture; changes to it are ADRs |
| 3 | [R4-UMRS-PLATFORM-OVERVIEW.md](R4-UMRS-PLATFORM-OVERVIEW.md) | What runs today and where the code lives |
| 4 | [adr/README.md](adr/README.md) | The architecture decisions and their status |
| 5 | [../ARCHITECTURE.md](../ARCHITECTURE.md) | The code map |
| 6 | [CANONICAL_MAP.md](CANONICAL_MAP.md), [index.md](index.md) | Topic to document; documents by role and directory |

## 2. By task

| You want | Read |
|---|---|
| What the platform is and who it is for | [getting-started/introduction.md](getting-started/introduction.md) |
| To use a deployed platform | [getting-started/user-guide.md](getting-started/user-guide.md), [user-manual/README.md](user-manual/README.md) |
| The stand-alone local stack | [user-manual/29-standalone-deployment.md](user-manual/29-standalone-deployment.md), [../deploy/standalone/README.md](../deploy/standalone/README.md) |
| Command and API reference | [user-manual/13-cli-reference.md](user-manual/13-cli-reference.md), [r4/TUI_REFERENCE.md](r4/TUI_REFERENCE.md), [r4/reference/sdk.md](r4/reference/sdk.md) |
| When something goes wrong | [getting-started/troubleshooting.md](getting-started/troubleshooting.md) |

## 3. When two documents disagree

The code wins; then the newest Accepted ADR; then the design and overview
documents; then guides. A disagreement is a defect: please open an issue or a
pull request that corrects the stale document.

Some documents in this tree describe the maintainers' internal process (waves,
ledger entries, state files, deployment to the R-CCS sites). You do not need
to follow it to contribute; [CONTRIBUTING.md](../CONTRIBUTING.md) is the
process for this repository. Documents dated before 2026-09-22 predate the
current design and are accurate only as of their date.
