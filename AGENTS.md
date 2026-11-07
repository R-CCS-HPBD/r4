# AGENTS.md — entry for coding agents working on R4

This file is for coding agents (Claude, Codex, Gemini, Copilot, ...) working
on a clone of the public R4 repository. CLAUDE.md, GEMINI.md and
.github/copilot-instructions.md point here.

**Read [CONTRIBUTING.md](CONTRIBUTING.md) first.** It is the contribution
guide for this repository: setup, the tests and lint that must pass, and how
pull requests are reviewed. Where any other document in this tree describes a
different process (ledger entries, state files, waves, gates that need private
infrastructure), that is the maintainers' internal workflow; you do not need
to follow it.

## Rules that matter here

1. **Tests and lint.** Run what CONTRIBUTING.md lists (ruff; pytest for
   `r4-contracts/` and `r4/`; `bun run typecheck` and `bun run test` for
   `packages/r4-tui-v3/`) before you propose a change. A fix ships with a test that fails on the old code.
2. **Small functions.** Keep cyclomatic complexity at or below 9 for every
   function you add or change, tests included.
3. **No secrets.** Never put credentials, tokens, private keys or personal data
   in code, tests, fixtures, logs or commit messages.
4. **Stay in scope.** Change only what the task needs; do not reformat or
   rename unrelated code. `umrs/` is a separate project (UMRS-org/umrs),
   included as a git submodule: send UMRS changes there.
5. **Security issues** go through [SECURITY.md](SECURITY.md), never a public
   issue or pull request.

## Orientation

- `r4/`: the client SDK, CLI and agent (Python).
- `r4-contracts/`: shared data contracts between R4 and UMRS.
- `packages/r4-tui-v3/`: the terminal console (TypeScript, bun).
- `vscode-extension/`: the VS Code extension.
- `deploy/standalone/`: the stand-alone local stack (`./r4-local up`).
- `docs/`: user manual, architecture notes and ADRs.
