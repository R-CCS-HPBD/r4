# External LLM Tooling Survey

This folder contains a comparative study of six open-source LLM agent / coding /
assistant projects that share scope with **r4 + UMRS**:

| # | Project | Type | Primary language |
|---|---------|------|------------------|
| 1 | [claw-code](01_claw-code.md) | Coding-agent CLI / harness | Python + Rust |
| 2 | [goose](02_goose.md) | Local on-machine engineering agent | Rust + Electron |
| 3 | [onyx](03_onyx.md) | Enterprise AI search & chat platform | Python + Next.js |
| 4 | [openclaude](04_openclaude.md) | Provider-agnostic Claude-Code fork | TypeScript |
| 5 | [copilot-cli](05_copilot-cli.md) | GitHub Copilot terminal agent | (closed; install only) |
| 6 | [openclaw](06_openclaw.md) | Personal multi-channel assistant gateway | TypeScript |

Then:

- [07_gap_analysis.md](07_gap_analysis.md) — features r4 + UMRS lack, or where
  another project's implementation is materially better.
- [08_implementation_plan.md](08_implementation_plan.md) — phased plan to fold
  the high-value items into r4 (and where appropriate, UMRS), aligned with the
  HPC-first, UMRS-as-orchestration-owner integration policy.

> Sources: each project's `README.md`, `AGENTS.md`, `VISION.md`, `CLAW.md`,
> `PARITY.md`, `PLAYBOOK.md`, and a directory walk of `src/`, `crates/`,
> `packages/`, `extensions/`, `skills/`, `backend/`, etc. checked into
> `tmp/<project>/`. No external network access required to reproduce.
