# ADR-065: Entry point and surface ladder — the console is the entry point

**Status:** Accepted (declaration; demotions are social, deletions happen per wave)
**Date:** 2026-09-22 (ROADMAP 5.1, under maintainer proceed authority)
**Relates to:** ADR-057 (chat-turn authority, Proposed), ADR-046 (remote sessions), gap rows D-25 / D-30 in [docs/R4-UMRS-GAP-ANALYSIS-2026-09-22.md](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md)

## Context

R4 grew several user-facing surfaces: the terminal console (`r4 console`,
Console v3, ten routes), ~65 CLI groups, a Python companion REPL, a VS Code
extension, a web console (`packages/r4-web/`), and Jupyter magics. The design
states a terminal TUI as the single entry point, working over SSH, with
session control local and remote from the same UI (D-25, D-30). Surfaces
unmanaged by a ladder drift features in different directions (the class of
problems the gap sweep keeps finding).

## Decision

1. **Entry point: the console** (`r4 console`, Console v3 on OpenTUI). New
   interactive features land there first; it is the surface a scientist is
   handed. It already works over SSH to the front-end (backend over the dual
   transport, ADR-059).
2. **The CLI** (`r4 <group>`) is the scripting and automation surface — every
   interactive capability should have a CLI verb; the CLI is not where new
   interactive UX is designed.
3. *(Amended 2026-10-31 by [ADR-103](103-vscode-console-parity.md): the VS
   Code extension is no longer demoted; it becomes a second host of the
   console's bridge contract, with parity measured by a matrix. The REPL,
   the web console and Jupyter stay as written here.)*
   **Demoted (companion surfaces, no new features):** the Python companion
   REPL (kept as the REPL fallback), the VS Code extension, the web console,
   Jupyter magics. They keep working; improvements flow through the console
   and CLI contract, and each is retired only by a named later ADR when its
   last distinct capability has an equivalent in the console.
4. **Session control (D-30).** One session vocabulary everywhere:
   start/attach/fork/stop today ride the same store and the same UI.
   Chat sessions get **no** pause/resume verbs: the durable session journal
   (ADR-063) plus fork makes them redundant. Remote (venue) session
   pause/resume stays the ROADMAP 3.2 leg it is — a **job-level** verb on
   the durable `frontend_session` lane (suspend + resume with the same
   lease), surfaced identically in console and CLI; it is never a separate
   surface.
5. **Chat-turn authority** continues under ADR-057 (Proposed): the server
   turns accessor is the read authority; the console renders from it.

## Consequences

- Docs that call anything but the console the primary surface are wrong by
  policy (README already correct; area docs updated as waves touch them).
- New-feature review asks "console first?"; a PR growing a demoted surface
  needs the ADR-amendment conversation.
- D-25 closes as a declaration; D-30 stays Partial only for ADR-057's
  durable-authority rollout, not for pause/resume verbs (decided against).
