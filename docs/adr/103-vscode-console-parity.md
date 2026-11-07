# ADR-103: The VS Code extension is a second host of the console contract

**Status:** Accepted (2026-10-31, maintainer request "now we are at time to proceed vscode of r4 to have exactly the same functions as tui. can you plan for this and proceed?"; the sub-decisions D-1…D-7 were drafted by the agent under the 2026-09-24 delegation and are open to the maintainer's review as M-V1…M-V4 in the plan)
**Date:** 2026-10-31
**Relates to:** [ADR-065](065-entry-point-console-surface-ladder.md) (amends its decision 3 for the VS Code extension only), [ADR-042](042-acp-transport-honesty.md) (the `r4 acp` rail), gap rows D-25 and D-30, the console bridge contract in [TUI_REFERENCE §12](../r4/TUI_REFERENCE.md#12-bridge-rpc-contract-developers)

## Context

ADR-065 (2026-09-22) made the console (`r4 console`, Console v3) the entry
point and demoted the VS Code extension to a companion surface with no new
features. The extension stopped there: its last commit is 2026-09-16, while
`packages/r4-tui-v3` has taken about 300 commits since (chat tabs, remote
venues, the web-access question, team plans, the workflow DAG, the local
tool sandbox, typed bridge errors, the Esc ladder…).

Verified on `main` at `43dbd2f4` (2026-10-31):

- The console talks to the **companion JSONL bridge**
  (`python -m r4.companion.ui.bridge_server`, `r4/src/r4/companion/ui/bridge_server.py`,
  6,354 lines). It serves 173 RPCs, each with a concurrency class in
  `rpc_concurrency.py`; 42 of them have typed results generated into
  `packages/r4-tui-v3/src/bridge/rpc-types.gen.ts`.
- The extension's agent webview talks to a **different rail**, `r4 acp`
  (`r4/src/r4/acp.py`, 2,152 lines, about 50 custom verbs, ADR-042). Every
  console feature since September landed on the bridge, not on this rail.
- The extension also runs a **second agent loop in TypeScript** for the
  `@r4` chat participant (`vscode-extension/src/agent-engine.ts`), with the
  defects D1–D3 its own `ARCHITECTURE.md` lists.
- 103 of the 155 modules in `packages/r4-tui-v3/src/lib` and `src/bridge`
  import nothing from Solid, OpenTUI, Bun or the console stores: they are
  plain TypeScript a webview can run.
- A real bridge boots under a scratch `HOME` with no gateway and answers
  `initialize` and `list_slash_commands` (probe 2026-10-31), so the
  extension can be tested against the real wire.

The maintainer now wants the extension to do everything the console does.
Building that on the ACP rail would mean re-implementing each console verb
a second time in Python and a third time in TypeScript, and the two would
drift again.

## Decision

**D-1. The extension leaves the demoted list.** ADR-065 decision 3 no longer
lists the VS Code extension. It is the console's second host: the same
functions, shown with VS Code's own affordances. The console stays the entry
point (it is what works over SSH) and new interactive features may still
land there first. The REPL, the web console and Jupyter stay demoted.

**D-2. One backend contract: the extension speaks the console's bridge.**
The extension host spawns the same `r4.companion.ui.bridge_server` process
the console spawns, with the same argv, and relays its lines to the
webview. It reads typed results through the generated `RpcResultMap`. The
extension adds no verb of its own to the bridge unless the console needs
it too. The `r4 acp` rail is frozen (fixes only) from this ADR on; when the
bridge path covers what the extension uses ACP for (plan wave VP-3), the
extension stops spawning it, and a later ADR decides whether the rail
itself is retired or kept for the ADR-042 standard adapter.

**D-3. One client core, two renderers.** Pure console logic (slash
catalog, interaction modes, permission rules, queue rail, session filters,
DAG layout, workflow parameters, prompt history, error hints…) is imported
by the extension, not rewritten. Until the modules move into a package of
their own, the extension imports them by relative path from
`packages/r4-tui-v3/src/`, and `vscode-extension/test/shared-modules.test.mjs`
walks their import graph and fails if any of them imports Solid, OpenTUI or
Bun or calls `Bun.*`. (The extension's `tsconfig.json` uses
`moduleResolution: "Bundler"`, which is how esbuild builds it, so it can
import the console's ESM sources.)

**D-4. Parity is measured, not claimed.** `vscode-extension/parity/console-parity.json`
lists every console function: each slash verb from the catalog manifest
and the bridge's registry fixture, each pane, and a curated list of
interactions (cards, overlays, status chips, keys). Each row has a VS Code
status: `done`, `partial`, `native` (the same function through a VS Code
feature, e.g. the integrated terminal for `/term`), `n/a` (a terminal
concern with no editor meaning, e.g. OSC 52 or kitty keys, with its reason)
or `missing`. A test fails when a console row is unclassified, when a
`done`/`partial`/`native` row names no existing evidence file, or when the
`missing` count rises above the recorded ceiling. The ceiling only goes
down, except in a commit that adds console rows, which raises it by those
rows' number and says so.

**D-5. Native affordances over imitation.** Where VS Code has the
function, the extension uses it: the integrated terminal for the Terminal
pane, the diff editor and SCM view for Code view and modified-files diffs,
editor tabs for chat tabs, `vscode.env.clipboard` for copy, contributed
keybindings for the keymap, and the VS Code color theme instead of the
thirteen console palettes. Each of these rows is `native` in the matrix
with the VS Code feature named.

**D-6. One agent loop.** The `@r4` chat participant stops running its own
TypeScript loop: it forwards to a bridge session like the webview does
(plan wave VP-8). Until then it is frozen. The language-model provider
and the MCP definition provider stay: they serve Copilot, not R4's own
agent, and have no console counterpart.

**D-7. Editor tools are an extension extra, kept behind the bridge.** The
ACP rail lets the agent call back into the editor (`workspace.diagnostics`,
`terminal.run`, the edit-review flow). The bridge has no such callback.
The bridge path starts without it (the agent's own file and shell tools
work on the same workspace); returning it needs a bridge verb the console
can ignore, added in its own wave with this ADR amended.

### Why

- One wire means a console fix is a VS Code fix: the session store, the
  approval ladder, venue and sandbox rules, typed errors and concurrency all
  live in the bridge already.
- A measured matrix keeps "exactly the same functions" checkable by anyone
  and makes drift fail CI instead of surfacing in a user report.
- Imitating a terminal inside an editor (OSC 52, mouse reporting, kitty
  keys, palettes) would be work with no user value; the editor already has
  those functions.

## Consequences

- A console PR that adds a slash verb, a pane or a matrix interaction must
  add its matrix row in the same PR (any status). The VS Code CI job runs
  the matrix test, so an unclassified row fails there.
- The extension needs the r4 Python package on the machine, as the console
  does. Remote use goes through VS Code Remote-SSH (the extension then runs
  on the front-end, next to `r4`).
- The webview grows a large share of the console's UI. Its React code stays
  the renderer; only logic moves to shared modules.
- The `r4 acp` rail and the TypeScript agent loop become dead weight once
  VP-3 and VP-8 land; their removal is planned, not open-ended.
- NOT in scope: the web console (`packages/r4-web`), the REPL, Jupyter; a
  standard ACP adapter (ADR-042 §2); new bridge features that the console
  does not have.

## Alternatives Considered

- **Grow the ACP rail to match the bridge** — two Python servers for one
  feature set; every console verb would be built twice and the rails would
  drift again, which is how the extension fell behind.
- **Embed the console in a VS Code terminal and stop there** — exact parity
  on day one, and the plan ships it as a fallback command (VP-0), but it is
  a terminal in an editor, not an editor integration: no editor tabs,
  diff editor, SCM, problems or clipboard.
- **Run the console's Solid stores in the webview** — the stores import
  OpenTUI renderables and Bun APIs; untangling them is a console rewrite.
  Sharing the pure modules (D-3) gets most of the value without it.

## Rollout-status (2026-10-31)

Wave ledger (117), `wave/vscode-parity`.

- D-1, D-4: landed (VP-0). `vscode-extension/parity/console-parity.json`
  (177 rows: done 1, partial 28, native 7, n/a 8, missing 133; ceiling 133),
  `scripts/parity-matrix.mjs`, the generated `PARITY.md` and
  `test/parity-matrix.test.mjs` (5 tests). `R4: Open Console in Editor`
  runs `r4 console` in an editor-area terminal.
- D-2: transport landed (VP-1), not yet used by the webview.
  `vscode-extension/src/bridge/companion-bridge.ts`, `resolve-python.ts`,
  `rpc.ts`; settings `r4.pythonPath` and `r4.agent.backend` (default `acp`).
  `test/companion-bridge.test.mjs` (17, fake process) and
  `test/companion-bridge-live.test.mjs` (4, the checkout's real bridge under
  a scratch HOME; skips without an r4 Python).
- D-3: started. The wire moved out of the console's `jsonl-client.ts` into
  `packages/r4-tui-v3/src/bridge/wire.ts` (re-exported, no behaviour change:
  console `bun run check` 2,822 pass / 37 skip / 0 fail + smoke); the
  extension imports `wire.ts`, `lib/terminal-safe.ts` and `bridge/rpc.ts`
  (with `rpc-types.gen.ts`); `test/shared-modules.test.mjs` guards them.
- D-5, D-6, D-7: open (plan waves VP-2…VP-8).

## Rollout-status (2026-10-31, later: VP-2a)

Wave ledger (120), `wave/vscode-parity`.

- D-2: the agent view's chat core runs over the bridge when
  `r4.agent.backend=bridge`:
  - `src/bridge/bridge-agent-host.ts` and `bridge-view-router.ts`, routed from
    `agent-view.ts` through a new `dispatchMessage`; the old 40-case
    `handleMessage` is unchanged;
  - turns, cancel, inline approval / question / plan cards, typed
    conflicts, session verbs, mode and model, slash lines through
    `dispatch_slash_command`.
  - Two facts the real bridge showed: `/new` keeps the session record, so a
    new session restarts the bridge until VP-3's tabs; delete archives.
- D-3: `packages/r4-tui-v3/src/lib/turn-reply.ts` (the reply fold, out of
  `adapters/companion-chat.ts`) is the second shared module. Tests:
  console `tests/turn-reply.test.ts` (5), and the console's turn suites
  unchanged (38).
- D-4: matrix missing 133 → 68 (done 12, partial 82). The registry slash
  verbs count as partial: their output is plain text until VP-2b's cards.
- D-7 (editor tools) unchanged; the bridge path still runs without them.

## Rollout-status (2026-11-01: VP-2b part 1)

Wave ledger (122), `wave/vscode-parity-vp2b`.

- D-2: queue while streaming, `!cmd`, message verbs (resend, edit, fork,
  save), citations, changed files and todos over the bridge.
- D-3: two more shared console modules, each split out of console code with
  re-exports (no behaviour change): `lib/queue-rail.ts` (the queue rail on
  any bridge client; `mayHaveLanded` now also recognises the error by name)
  and `lib/shell-relay.ts` (the `!cmd` preview, relay framing and key
  masking).
- D-5: a changed file opens in VS Code's diff editor, a cited passage in an
  editor tab (`src/bridge/editor-hooks.ts`).
- D-4: matrix missing 68 → 63 (done 19, native 8).

## Rollout-status (2026-11-01, later: VP-2b part 2)

Wave ledger (123), `wave/vscode-parity-vp2b2`.

- D-3: the webview now imports console modules too (`webview-ui/src/lib/console-shared.ts`):
  - `lib/mention-query.ts`, out of `file-mentions.ts`;
  - `lib/paste-tracker.ts`, out of `paste-chips.ts`, which keeps its Bun width helpers;
  - `mention-chips.ts`.

  The host adds `lib/save-args.ts`, out of `commands/save-command.ts`. `test/shared-modules.test.mjs` walks the webview's imports as well.
- D-2/D-5: `@` mentions, paste chips, slash output cards, the typed `/changes` `/fork` `/save`, and the one-time publish confirmation (a modal); full tool results on expand.
- D-4: missing 63 → 61, done 19 → 63.

## Rollout-status (2026-11-01, later: VP-3a)

Wave ledger (125), `wave/vscode-parity-vp3a`.

- D-2:
  - the local tool sandbox: notice, `sbx:` chip, `/sandbox`;
  - web access: label, `/web`, and the question before a session's first turn (ADR-099 D-2);
  - Sessions filters sent to the server, badges, export.
- D-3: `lib/sandbox.ts` and `lib/web-access.ts` run in the extension host; `ChipState` moved to the plain `lib/chip-state.ts` (re-exported by `components/status-chip.tsx`) so the sandbox module no longer reaches a Solid component.
- D-5: the web-access question is a native QuickPick plus an input box; an export opens in an editor tab.
- D-4: missing 61 → 57, done 63 → 67.

## Rollout-status (2026-11-01, later: VP-3b part 1)

Wave ledger (126), `wave/vscode-parity-vp3a`.

- D-2: chat tabs. Each editor tab runs its own bridge and host (cap 8); the title follows the session and its marks; a closed tab reopens into its session; `/tab` has the console's verbs.
- D-5: a tab is a `WebviewPanel`. The `+` on the agent view opens one, and so does ⧉ on a Sessions row. The sidebar keeps Studio, Knowledge and the management cards.
- D-4: missing 57 → 53, done 67 → 71.

## Rollout-status (2026-11-01, later: VP-3b part 2)

Wave ledger (127), `wave/vscode-parity-vp3a`.

- D-2: sessions on the server. `/venue` has the console's verbs; the `remote:` chip shows the lease; a server turn is watched over `session.venue_sync`; remote approvals and questions open as cards; ⏹ on a Sessions row stops a runtime.
- D-3: the extension runs the console's `lib/venue-runtime.ts` as it is. `questionIdFromDetail` moved there from `bridge/venue-bridge.ts`, which re-exports it, so the host reads the worker's heartbeat with the console's rule.
- D-5: the source picker is a QuickPick; push and unpush ask with a modal.
- D-4: missing 53 → 50, done 71 → 74.

## Rollout-status (2026-11-01, later: VP-3b part 3)

Wave ledger (128), `wave/vscode-parity-vp3a`.

- D-2: a new chat asks Local or Server, as the console's venue picker does; a Sessions row forks and lists its turns. VP-3 is complete except VP-3c (the default flip).
- D-3: `sessionTurnLine` and `turnRowsOf` moved from `bridge/session-bridge.ts` to the pure `lib/session-turns.ts` (re-exported).
- D-4: missing 50 → 49, done 74 → 76 (venue picker; the Sessions pane is done).

## Rollout-status (2026-11-01, later: VP-2c)

Wave ledger (132), `wave/vscode-parity-vp2c`.

- D-2: the edit and `/rewind` cut the session history before the resend (`session.truncate`), as the console does; `/btw`, `/bg`, `/explain`, `/palette`.
- D-3: `explainText` moved from `commands/files-command.ts` to the pure `lib/explain-text.ts`.
- D-4: missing 49 → 44, done 76 → 81. Correction: `interaction:transcript-cursor` was marked done in VP-2b while its edit did not cut the history; it is right now.

## Rollout-status (2026-11-01, later: VP-2d)

Wave ledger (137), `wave/vscode-parity-vp2d`.

- D-2: the slash popup and `/help` come from the bridge's live catalog; the Esc ladder; registry hints on errors; `/search-server`.
- D-3: the search-server texts moved from `commands/search-server-command.ts` to the pure `lib/search-server-text.ts` (re-exported).
- D-4: missing 45 → 43, done 81 → 87.

## Rollout-status (2026-11-02: VP-2e)

Wave ledger (156), `wave/vscode-parity-vp2e`.

- D-2: `/models` picker over `list_models`; `/mode <name>` and `/mode write-plan on|off`.
- D-4: done 87 → 89 (`/models`, `/mode` move from partial to done).

## Rollout-status (2026-11-02, later: VP-4a)

Wave ledger (157), `wave/vscode-parity-vp2e`.

- D-2: `/traces`, `/provenance`, `/sweep`, and `/loops`, with a one-shot detail.
- D-3: their texts moved from `commands/slash.ts` and `commands/sweep-command.ts` to the pure `lib/ops-text.ts`; `slash.ts` lost about 70 lines.
- D-4: missing 43 → 39.

## Rollout-status (2026-11-05: VP-4b part 1)

Wave ledger (193), `wave/vscode-parity-vp4b`.

- D-2: `/teamup` request verbs as output cards; arming and live events follow in part 2.
- D-3: the teamup notice texts moved from `stores/teams.ts` and `commands/wave-intercepts.ts` to the pure `lib/teamup-text.ts`. The list and status cards are new there, for the extension.
- D-4: `/teamup` missing → partial; `/team` stays partial with a new note.

## Rollout-status (2026-11-05, later: VP-4b part 2)

Wave ledger (199), `wave/vscode-parity-vp4b2`.

- D-2: `/teamup on|off` and the armed fire; live team and plan rows (keyed notices that update in place).
- D-3: the arming, fire, plan-refusal and live-row texts moved from `commands/wave-intercepts.ts`, `stores/teams.ts` and `stores/index.ts` to the pure `lib/teamup-text.ts`.
- D-4: missing 38 → 36; done 91 → 93.

## Rollout-status (2026-11-05, later: VP-4c)

Wave ledger (200), `wave/vscode-parity-vp4c`.

- D-2: `/work`, `/workflows` and `/infra` as cards; `/work show|run <workflow>`.
- D-3: `WorkflowDefStep`, `WorkflowDefFile` and `WorkflowDefinitionDetail` moved from `stores/umrs-workflow.ts` (re-exported) to `lib/workflow-def-types.ts`; `workflow-card-rows.ts`, `workflow-params.ts` and `workflow-exec-graph.ts` import from there.
- D-4: missing 36 → 33.

## Related

- [ADR-065](065-entry-point-console-surface-ladder.md) — amended (dated note there)
- [ADR-042](042-acp-transport-honesty.md) — the frozen rail
- Plan: VS Code ↔ console parity
- [Console reference](../r4/TUI_REFERENCE.md), [extension implementation map](../../vscode-extension/ARCHITECTURE.md)
