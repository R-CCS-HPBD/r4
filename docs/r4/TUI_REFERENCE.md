# R4 Console v3 Reference

**Updated 2026-09-22 (design reset; rewritten for Console v3); §6 modes and approvals rewritten 2026-10-31 (ADR-104); launch order updated 2026-09-25; workflow definition card 2026-10-02; Terminal pane 2026-10-03; bridge RPC contract 2026-10-18; stabilization corrections 2026-10-21 (Esc ladder, approval Esc, paste chips, Logs keys, status line); web-access question and `/web` 2026-10-23 (ADR-099); `/workflow graph`, the workflow badge and Alt+W 2026-10-24; Settings Preferences 2026-10-27 (issue #217); local tool sandbox (`/sandbox`, the sandbox notice, the `sbx:` chip) 2026-10-30 (ADR-101); the shared bridge wire and the VS Code parity matrix 2026-10-31 (ADR-103, §12).** This page
documents the current interactive surface: **Console v3**, a Bun + SolidJS +
OpenTUI terminal application in
[`packages/r4-tui-v3/`](../../packages/r4-tui-v3/README.md). The JS (Ink)
TUI this page used to describe was removed on 2026-09-11
([ADR-014](../adr/014-python-gateway-retirement.md)). Nothing below is carried
over from it: every route, key and slash command was checked against the tree
on the date above.

Why the console matters: the design makes the terminal UI the single entry
point for researchers, on a laptop or over SSH to the front-end
([R4-UMRS-DESIGN, "R4: the researcher-facing agent environment"](../R4-UMRS-DESIGN.md)).
The gap analysis rates that as *Partial*: `r4 console` is the primary
surface, but a Rich REPL, a VS Code extension, a web console, Jupyter magic
and about 65 CLI groups still exist beside it
([D-25](../R4-UMRS-GAP-ANALYSIS-2026-09-22.md); D-30 covers "session control,
local and remote, same UI"). This page says what the console does today; the
[CLI reference](reference/cli.md) covers scripting.

Contents: [Launch and install](#1-launch-and-install) ·
[The frame](#2-the-frame) · [Panes](#3-panes-the-ten-routes) ·
[Keyboard](#4-keyboard) · [Palette and slash commands](#5-command-palette-and-slash-commands) ·
[Modes and permissions](#6-interaction-modes-and-the-permission-ladder) ·
[Sessions](#7-sessions-local-and-remote) · [Knowledge](#8-knowledge-in-the-console) ·
[Theme, motion, clipboard](#9-theme-motion-clipboard) ·
[Environment](#10-environment-variables) · [Not in the console](#11-what-is-not-in-the-console) ·
[Bridge RPC contract](#12-bridge-rpc-contract-developers)

---

## 1. Launch and install

```bash
r4 tui install       # download r4-tui-<platform> + .sha256, verify, install
r4 tui status        # show what r4 console will run (checkout source or binary)
r4 console           # launch against the live backend
r4 console --demo    # fixture data + demo visuals (same as --offline)
r4                   # no subcommand: Console v3 when available, else the Rich REPL
```

**Launch resolution** (`plan_console_launch` in `r4/src/r4/_cli/console.py`; 2026-09-25):
`R4_TUI_BIN` (explicit binary; wins over everything) → the Console v3 source of the
checkout this `r4` is imported from, run with bun (`bun --preload
@opentui/solid/preload src/cli.tsx`), when its `node_modules` is installed (`make
install` / `make install-client` / `make tui-deps` run `bun install --frozen-lockfile`)
and bun ≥ 1.3.14 is found on `PATH` or `~/.bun/bin` → `~/.local/share/r4/bin/r4-tui`
(`$R4_TUI_HOME/bin` when set; from `r4 tui install`, for installs without a
checkout) → a `packages/r4-tui-v3` under the cwd run with bun. A clone always runs its
own TUI: the release binary never shadows a set-up checkout (force it with
`R4_TUI_BIN=~/.local/share/r4/bin/r4-tui r4 console`). The launch prints the checkout
and its last TUI commit, and warns when `node_modules/.r4-deps-stamp` no longer
matches `package.json` + `bun.lock` (run `make tui-deps`). Nothing is compiled into
`.venv/bin` — a copied binary would go stale on the next pull.
Release assets are `r4-tui-linux-x64`, `r4-tui-linux-x64-baseline`, `r4-tui-linux-arm64`,
`r4-tui-darwin-arm64` and `r4-tui-darwin-x64`, each with a `.sha256`;
`r4 tui install` resolves the newest release including prereleases (`R4_TUI_VERSION`
pins a tag; `R4_TUI_DIST_BASE_URL` overrides the base) and picks the asset for the
host: Intel Macs (darwin/x86_64) get `r4-tui-darwin-x64`; the baseline build (x86_64
CPUs without AVX2) is never picked automatically. (2026-11-07: this line said
macOS x86_64 runs the arm64 build under Rosetta; Rosetta translates x86_64 to arm64,
never the reverse, so that build could not start there.) If no release is published,
build locally with `cd packages/r4-tui-v3 && bun run scripts/build-bin.mjs` and `mv` (never `cp` over a live
binary on macOS) `dist-bin/<asset>` to the user bin path.

**`r4 console` flags** (`r4/src/r4/_cli/console.py`): `--demo` / `--offline`
(sets `R4_TUI_BACKEND=offline` and `R4_TUI_DEMO=1`), `--backend cli|offline`.
The launcher exports `R4_TUI_PYTHON` (the launching interpreter) and pins
`R4_TUI_WORKSPACE` to the launch directory; Ctrl+C at the wrapper ends the
console quietly with exit 130.

**Other entry points.** `r4`, `r4 companion` and `r4 chat --companion` all go
through `launch()` (`r4/src/r4/companion/ui/__init__.py`): Console v3 when
`console_available()` (installed binary, or checkout + bun), otherwise the
Rich REPL. `--legacy-tui` forces the Rich REPL. `r4 -p "…"` runs a headless
one-shot turn and never opens the console. The root flags `--mode`,
`--allow-all-tools`/`--yolo`, `--fs-root`, `--secure` and `--model` are parsed
but the v3 launch path does not receive them — set mode, approval policy and
model in-session (`/mode`, `/trust`, `/models`). `--source NAME`
is applied to the active configuration before launch and is honoured.

**Dev loop** (in `packages/r4-tui-v3`): `bun install`, `bun run start`,
`bun run src/cli.tsx --demo | --version | --help | --smoke`, `bun run check`
(typecheck + tests + headless smoke). Requirements: Bun ≥ 1.3.14, Node ≥ 22
for tooling, the `r4` CLI on `PATH` for live data.

**Backend.** `R4_TUI_BACKEND=cli` (chosen automatically when `r4` is found):
chat, sessions, approvals and most slash verbs ride the Companion JSONL bridge
(`r4/src/r4/companion/ui/bridge_server.py`); endpoints, models, jobs and
skills are polled from the CLI every 8 s (`r4 endpoint list --json --all`,
`r4 model list --json`, `r4 job list --json --limit 20`, `r4 skill list
--json`) with exponential backoff and reconnect notices. `offline`: canned
chat, fixture inventory, backend actions answer `(offline) would …`.

---

## 2. The frame

- **Box chrome** (2026-09-29, floats 2026-09-30): panels no longer draw a
  full frame. One solid bar on the **left edge only** (heavy border) marks
  each box, colored by its category/focus accent; separation comes from
  background tones — `bgPanel` (in-screen base) and `bgHover` (raised:
  focused or hero panes; also the selection-row tone inside floats). Panel
  titles render as a first-row text inside the box (they used to sit on the
  removed top border). **Floating surfaces — overlays, pickers, palettes,
  popups — draw no lines at all** (2026-09-30): they sit on a third tone,
  `bgActive`, which separates the layer from the content below. Shared
  implementations: `src/ui/Panel.tsx` (panels) and `src/ui/Floating.tsx`
  (floats). The title-screen input strip is borderless too.
- **Zone colors** (2026-09-30): three background zones, pinned per theme by
  `tests/theme-zone-separation.test.ts` — **user input** = `msgUserBg`
  (composer strip, user bubbles, every input row incl. those inside floats),
  **agent output** = `msgAssistantBg` (assistant prose, stays near the
  backdrop), **floating panes** = `bgActive`. All 13 themes retuned so the
  zones stay visibly apart (five themes previously had
  `msgUserBg === bgActive`, which swallowed input fields on overlays).
- **Transcript spacing** (2026-09-30, pinned by `tests/chat-spacing.test.tsx`):
  the user bubble FLOATS — one blank row above and two below, so it never
  touches the reply/tool activity that chronologically follows it; the
  assistant header row (`R4 HH:MM:SS @model`) hugs the reply body — leading
  blank lines of model output are trimmed at render (display-only) and an
  empty pre-token streaming reply paints no placeholder row, so no gutter
  ever opens between the model name and its content.
- **Transcript visibility** (2026-10-19, pinned by `tests/markdown-wrap.test.tsx`
  and `tests/chat-spacing.test.tsx`): every message cell carries a
  role-tinted LEFT SPINE (the single-style border, `│`) so user input /
  agent reply / system markers read as separate blocks, and one blank row
  above — consecutive replies never weld. Long prose lines WRAP at the live
  chat column at word boundaries (hanging indent under list/quote markers)
  instead of being hard-clipped at the right edge; code fences and tables
  keep their horizontal-clip contract. The wrap column accounts for the
  full cell chrome (panel bar + padding + spine + scrollbar column) — rows
  that overflow the real column corrupt mid-span in the engine, so the
  margin is load-bearing. Tool-action groups, files-modified cards and
  error cards breathe one blank row under the reply text.
  2026-10-21 stabilization (pinned by `tests/chat-cell-width.test.tsx` and
  `tests/span-row-clip.test.tsx`): the 2026-10-19 code above did not ship
  until this date (it sat uncommitted in a checkout). Every width inside a
  cell (prose wrap, tool rows, diffs, files card) now comes from one text
  column, chat column − 6, with no 40-column floor. The chat column follows
  a dragged pane width. The wrap counts display columns, so CJK counts
  2 per character, and it breaks between wide characters with kinsoku
  (no row starts with `、。」…`). Coloured span rows (highlighted prose,
  code) clip at the right edge and never shrink each span.
- **Title screen.** The R4 logo with a chat input: Enter sends the first
  message and enters the console, Esc skips. It stays until you do one of
  those (no timer).
- **Chat** is always mounted and owns the keyboard by default. The composer
  prefix follows the interaction mode (`you ›`, `plan ›`, `auto ›`).
- **Chat tabs** (one row above chat, 2026-09-29): opencode-style parallel
  session tabs. Each tab owns one companion session on its OWN JSONL bridge
  process, so turns run concurrently — a backgrounded tab keeps working; its
  strip cell shows `●` while busy and `!` when an approval/question/error
  landed while you were elsewhere (`+N` = absorbed background events).
  Switching swaps the full chat scope (transcript, queue, cost, thinking,
  pending approval/plan/ask, model, venue, session id); the live pump of a
  backgrounded turn suspends and resumes losslessly because the bridge-side
  generator buffers deltas meanwhile. Cap: 8 tabs (one python bridge each).
  Tab titles adopt the FIRST plain typed prompt immediately (first 60 chars
  of the first line; slash commands and `!cmd` never name — verbs are not
  conversation; teamup goals do name), then refine to the SERVER session
  name once a real one exists. A session still wearing the server's
  create-time placeholder (`Session YYYY-MM-DD HH:MM`, the exact
  `AUTO_SESSION_NAME_RE` shape) gets renamed SERVER-side once per run from
  its first user prompt; placeholders never overwrite a good title, and
  human names like "Session notes" are never rewritten. Title sync
  refreshes after turns, on switches, and on any `/sessions` fetch — the
  `[Project context]` wire block that server auto-naming can leak is
  stripped before display, and an empty cleaned name never overwrites.
  Cells are capped at 28 columns (opencode's 224px class); a longer title
  marquee-scrolls on the ACTIVE tab only (background tabs ellipsize, and
  the ticker sleeps unless something overflows).
  `/tab` lists and jumps; `/sessions resume` and the Sessions pane now open
  sessions in their own tab (deduped; *2026-10-22:* `/sessions resume <id>` on
  the tab that already holds that session reloads it from the store — the
  recovery after a `session_conflict`; its `error.data` wire shape, `stage`
  included, is in §12). Closing a tab shuts its bridge down;
  **auto titles** (2026-09-29): after a session's first completed turn the
  bridge asks the model for a ≤8-word title and renames a still-default
  (`Session YYYY-MM-DD HH:MM`) session — the tab head then shows the topic on
  every bridge (`session_title` event). User-set names are never overwritten.
  `Ctrl+Shift+T` reopens the last closed ones by resuming the session from
  the server journal (never client memory).
- **Detail pane** (one at a time) is summoned by a pane command or the
  palette and closed with Esc. It docks right of chat at 45 % (≥ 140
  columns) or 38 % (100–139); under 100 columns it becomes a full-width sheet
  over chat and the status line notes `pane: sheet`.
- **Terminal pane** (2026-10-03; pointer model + drag-resize revised same
  day): `/term` docks a real PTY shell right of chat — Bun's spawn-terminal
  option plus `@xterm/headless` emulation, rendered with truecolor cell
  styling (`src/terminal/`, `src/components/terminal-pane.tsx`). The shell
  starts in `R4_TUI_WORKSPACE` under `$SHELL` (fallback `/bin/sh`), sizes to
  the pane's MEASURED box (never the width estimate) and follows resizes via
  SIGWINCH. **Focus is by mouse CLICK only**: the pane opens unfocused
  (typing keeps reaching the composer), a click inside focuses the shell,
  a click in the chat column releases it — no key moves this focus. While
  focused **every key goes to the shell** — letters, arrows, Tab, Esc (vim
  works), Ctrl+C (real SIGINT), paste (bracketed-paste when the shell
  asks); Ctrl+Q, F12 and Ctrl+Alt+M (mouse-reporting toggle, the
  mouse-less exit hatch) still pass through. The focus state is visible:
  the shell cursor draws as a **solid block when focused, hollow when
  not**, and the pane title reads `Terminal · click to focus` until you
  click in. Shift+PgUp/PgDn scrolls the view through the scrollback. Esc
  follows the normal pane rule once focus is out and closes the pane —
  **the shell keeps running** and `/term` reattaches to the same buffer.
  `/term new` restarts fresh, `/term kill` ends the shell and closes the
  pane. An exited shell shows an honest footer (`r` restarts, Esc closes).
  **Drag the pane's left bar** to resize it (the width is
  `workspace.paneWidthOverride`, clamped so chat keeps ≥ 40 cols; session
  scoped). POSIX hosts only (win32 reports unsupported).
  `/term share [lines]` relays the buffer tail to the session agent through
  the same relay as `!cmd` (framed "not in your tool history", queues
  behind a running turn) — capture is user-initiated ONLY, never streamed:
  the shell is the raw user environment. Trailing blanks are trimmed,
  default 40 lines (max 500), 20000-char cap with an honest truncation
  marker (2026-10-03).
- **Status line** (bottom, one row): left = the live turn indicator (phase
  glyph, running tool, tool/agent counts, elapsed) or, idle,
  `profile/project · agents n/m` (plus `⚠︎N` when `/health authz` found
  flags); middle = `model · mode:<chat|plan|autopilot>` (`autopilot(policy)`
  when an organisation policy narrows autopilot; ADR-104, the `perm:` badge
  is gone); right = chips `n:<unread>`, `gw:`, `umrs:`, `hpc:`,
  `remote:<state>[/phase] · lease <t>` (frontend sessions only), the
  sandbox chip `sbx:on` / `sbx:off` / `sbx:n/a` (local sessions only; `n/a`
  = no usable backend on this machine; *2026-10-30, ADR-101*, see §7), the
  workflow badge `▶ N workflow(s) · ◐ done/total` while UMRS workflow
  executions run *(2026-10-24: N counts the live executions in the Work
  pane's project scope — `A` there shows all projects, and the badge
  follows; a run UMRS now answers "not found" for stops counting; the step
  progress is the most recent running one's, read from the record the
  store already polls: done = succeeded or skipped steps, total = the
  definition's step count, read once per workflow when a run is first
  seen, shown `n+` while it is unknown; hidden when none runs and below
  100 columns, and dropped before the team and mid-turn identity chips;
  Alt+W opens that execution's live DAG overlay)* and a
  resource summary when the bridge reports one. *(2026-10-21)* Narrow
  terminals: the middle segment never shrinks; the identity head keeps
  its room; a chip that does not fit is dropped whole, in priority order
  (never cut mid-label); the CPU/RAM/host chip yields first (hostname,
  then the whole chip); the left text ends in `…` when cut.
- **Inline cards in the transcript**: tool-approval card (for
  `file_write`/`file_edit` asks it shows the SERVER-COMPUTED unified diff of
  the pending change — side-by-side at ≥ 110 columns — so you approve the
  actual edit, opencode EditBody parity; other tools stay
  description-only), agent question
  (`ask_user`), plan card, error cards, folded tool-call roll-ups, session
  todos, modified-files card (`✎ N files modified` under any assistant reply
  whose `file_write`/`file_edit` calls changed content — per-file diffs are
  VISIBLE BY DEFAULT, capped at 30 lines with a show-more verb, side-by-side
  at ≥ 110 columns; a `file_write` without an embedded diff shows the full
  write content as a styled code block; every row collapses on click).
- **Code fences are shiki-highlighted** (truecolor tokens, 29 pre-loaded
  languages, JS regex engine so the binary ships no WASM; regex fallback
  until ready / for unknown languages). One renderer serves streaming and
  finalized messages — the previous native `<markdown>` streaming path
  painted fenced code with zero color and could swallow fence rows.
- **Plan tab** (Activity Console): structured session-plan graph — steps
  from the bridge's task-graph mirror / `get_session_plan` RPC (todo_write
  mirrored server-side into the session TaskGraph with sequential
  `depends_on` edges), dependency-layered rows (`└─`) with status
  glyph+color, plus umrs workflow executions launched via
  `workflow_run`/`workflow_upload`/`project_run` tools. Pre-plan backends
  keep the legacy todo-list fallback.

---

## 3. Panes (the ten routes)

`packages/r4-tui-v3/src/routes/` holds ten route files; the detail pane mounts
them by view id (`layout/detail-pane.tsx`). Three more views are not routes:
**Overview** (`/overview`, the context/inspector panel), **Logs**
(`/logs`, the Activity Console: Agent / Tools / Events / Term / Notifs /
Plan tabs) and **Terminal** (`/term`, the PTY shell pane — see §2). Open the
Settings pane from the palette ("Open pane: Settings"); `/settings` is the
preferences verb, not the pane.

| Pane | Open with | Shows | Reads |
|---|---|---|---|
| **Work** | `/work` (`/workflows`; bare `/workflow`, bare `/endpoint`, `/teamup` land here too) | One sectioned list: approvals (pending tool card + runtime gates), local agent runs, remote jobs with attempt/restart chips, teams (team sessions with agents and budget, and team plan runs with status, agents ok/null and tokens — ADR-092, 2026-10-11), endpoints (host:port, tier, cold-start), assets, workflow executions + trigger catalog, research loops (phase/iter/goal, 2026-09-27). ↑↓, Enter = detail/overlay — an execution row opens the **mission overlay** (per-step status/wall time/GPU cost + sacct-measured footer; live while running; 2026-09-27), jobs `r` retry / `c` cancel / `t` traces, workflow-execution `T` traces / `x` cancel, catalog Enter = the **workflow definition card** (below) / `t` trigger — runs at once, or puts the trigger line in the composer when the workflow needs values (2026-10-02), teams `r`/`p`/`R`/`c`/`l`/`o`, team plan rows Enter = the run's result with one row per agent / `c` cancel (your own action, so no approval card; an agent's `team_plan_cancel` still asks), loops `x` stop. A pending approval row names the plan agent or team role that asks. | CLI job poll + bridge `project_job_event`; agent-runtime store; `teamup.*`, `team.runs`/`team.plan_result`/`team.plan_cancel`, `workflow.list/run/status/cancel/package`, `traces.job`/`traces.execution`, `loops.list`/`loops.stop`, `assets.snapshot`/`assets.patch` bridge verbs. |
| **Tasks** | `/tasks` | Plan tasks with status glyphs. ↑↓; Enter or `a` drafts a chat question about the task; `r` → Research; `k` → Knowledge. | `workspace.tasks` from the project plan. |
| **Knowledge** | `/knowledge` | Build strip (plan → ingest → extract → coverage → graph, plus a quota line), Verify box + answer card (evidence ids, confidence), graph lane (server canonical spine, else local plan projection, with the agent proposal inbox), Knowledge Bases list (the server's `/k2/artifacts` rows, the same list as `r4 knowledge ls`, each with who added it; K1.3 2026-10-24), Ingestion / Coverage / Readiness cards, Research Plan / Loop / Sessions cards. ↑↓; Enter on a file prefills Verify; Enter in Verify asks; Esc returns to the list; with the pane focused, `]` / `[` select an agent proposal and `a` / `r` approve or reject it. | Bridge `get_knowledge` poll; platform adapter `listGraphs` / `graphContent` / `graphProposals` / `reconcileKnowledge` / `catalogStats`; `get_governance_overview`. |
| **Catalog** | `/catalog` | ADR-031 science catalogue: search row (Enter searches, Esc clears), asset rows with consistency badge + cross-plane links, read drill with byte-bounded preview and lineage, inventory and consistency lines; then workspace assets (skills / MCP / sessions / code changes) from local stores. | `catalogSearch` (`/umrs/catalog/search`), `catalogRead`, `catalogStats`, `reconcileKnowledge`, `knowledgeLineage`. |
| **Sessions** | `/sessions` | Server session records: venue glyph `L`/`F`/`C`, state chip, sync badge (`local-only` / `remote-only` / `mirrored` / `degraded`), pinned `★`, archived `⛁`; filter chips `[n] new · [s] state · [v] venue · [A] archives`; footer verbs ↵ resume · r rename · p pin · d archive · x export · t stop remote (F rows). Rows and verbs are clickable. | `list_sessions` (filters ride the wire), `load_session`, `session.rename/pin/unpin/archive/fork/turns`, `session.venue_stop`. |
| **Loop** | `/loop` | Local workflow-builder demo (templates, step editor, gate approval, loop history) — functional only with `--demo` (Ctrl+A assemble, Ctrl+R run, `c` reset); live mode shows a redirect card to Research. | Nothing (local stores). |
| **Research** | `/research` | Mission Control, Agents monitor, Project Graph, Live verify, bottom tabs Ops / Strategy / Explain (Alt+1/2/3; Alt+Shift+N and Ctrl+Alt+Shift+N fallbacks). Tab / Shift+Tab cycle focus graph → verify → chat. Live verify answers stay in the pane (2026-10-18: the Ctrl+U "resolve as finding" key is retired; findings live in project verification reports, `/umrs/knowledge/findings`). | Agent-runtime store (mission control, hypotheses, agents), `workspace.goals`/`tasks`, knowledge verify. |
| **Code view** | `/codeview` (`/codereview`, `/git`) | Git change sets (A/M/D/R) with a per-file `git diff HEAD` preview — side-by-side old/new at ≥ 110 columns, unified below. Footer: ↵ diff · o `$VISUAL`/`$EDITOR` · r arm revert → R confirm · t `bun test`. | `git status` / `git diff` in `R4_TUI_WORKSPACE`. |
| **Terminal** | `/term` (`/terminal`), click to focus | Interactive PTY shell (`$SHELL` in `R4_TUI_WORKSPACE`) with truecolor rendering, scrollback (Shift+PgUp/PgDn), exit/restart footer. All keys go to the shell while focused; focus is by CLICK alone (in = pane, out = chat column); block/hollow cursor shows the state; drag the left bar to resize. | Local PTY (Bun spawn `terminal` option) + `@xterm/headless`; never touches the backend. |
| **Infrastructure** | `/infra` | Endpoints, Jobs and Models columns, counts and a load sparkline. Tab switches Endpoints ↔ Jobs; ↑↓ select; `s` stop endpoint, `c` cancel job, `r` retry failed job, `l` job details/logs. | The 8 s CLI poll (`endpoint list`, `job list`, `model list`). |
| **Settings** | palette → "Open pane: Settings" | **Preferences** first: completion bell and ambient event cards (this session; Enter or a click toggles), theme (saved; opens the `/theme` picker), model (this session; opens `/models`), permission mode (Enter shows `/settings matrix`), project approval rules (saved per project; `/settings rules`), status line (saved; `/statusline`). Each row names where its value lives; a line under the list says what Enter does. Then backend source, connection (gateway / UMRS / HPC), inventory counts, selectable Skills and MCP servers (↑↓, Enter detail), live keymap rows. With the pane focused, Tab cycles Preferences → Skills → MCP; Esc or a click leaves the pane. *(2026-10-27, issue #217: before, the pane was status and shortcuts only, and its "Tab panel" hint never worked because the global Tab took the key.)* | `list_skills`, `list_mcp_servers`, the keymap registry; the workspace store, `<configRoot>/theme.json`, project rules files, `<configRoot>/statusline`. |

**Subagent Work rows (2026-11-10, D-70 S1/S2).** Ordinary background and
synchronous Companion children use the backend `agent_id`, not a synthetic
console runtime ID. `agent_result` updates that same row to completed, failed
or cancelled; duplicate events are idempotent and a result that arrives before
its spawn creates one defensive terminal row which another spawn in the same
execution turn cannot reopen. A genuine follow-up increments `subagent_turn`,
updates the task and may run again; stale or duplicate results from an earlier
turn stay ignored. The runtime also retains child/parent session lineage. This
is only the existing Work-row correlation fix: the read-only child transcript inspector
and open-session action are S3/S4, not yet available. Child session rows and
redacted tool history are durable locally; ordinary session resume excludes
them so opening one cannot create a second writer.

**Parallel hypothesis tracks (ADR-066 amendment 2, surfaced 2026-09-27).**
A loop started with `--tracks N` shows a `t<N>` chip on its Work row, and
`/loops detail <id>` (or Enter there) lists each track with its hypothesis,
status (`active`/`rejected`) and turns — plain loops carry no track text
(honest absence).

**Workflow definition card (2026-10-01; run instructions 2026-10-02).**
Enter on a catalog row opens the registered workflow as one scrolling page
(↑↓ PgUp PgDn Home End, mouse wheel), built from the `workflow.package` RPC:

- **meta**: version, scope, owner, execution mode, resources, requirements,
  triggers, source path;
- **how to run**: the exact line, e.g.
  `/workflow trigger fig-read --param image=`, listing every parameter that
  needs a value. `r` puts it in the composer, closes the card and pane
  (the composer only takes keys with the pane closed) and says what to fill.
  Enter then runs it through the companion's `/workflow trigger`. That run
  joins the Work pane like a `t` run, scoped to `--project` or the active
  project;
- **parameters**: one row each, with name (`*` = needs a value), type,
  default, description, choices and, when not declared, where it was found.
  Sources are the `parameter_schema`, `{{ params.x }}` in a step config, the
  image a root vision/OCR step reads, and `config.get("x", default)` /
  `config["x"]` in the registered code. A key the step's own config sets is
  skipped, as are the reserved routing/credential keys. `project_id` and
  `input_files` come from the run itself;
- **graph**: boxes and arrows of the steps the runner executes. When no step
  declares `depends_on`, the steps chain in declaration order and the header
  says so. A shared line means every parent feeds every child. Lines that
  cross without joining are drawn as a jump (`─│─`). Labels shrink, then the
  graph falls back to the step list, when the terminal is too narrow;
- **steps**: what each waits for, its entrypoint, a config summary, and
  model_type/requires;
- **code**: registered files, collapsed. ←→ pick a file; Enter or a click
  opens it inline, at most 400 lines.

Esc closes the card and leaves the Work pane, which takes the keys again.

**Live execution DAG (2026-10-20; direct entry 2026-10-24).** The mission
overlay of an execution draws the definition's topology with one status
glyph per step (✔ ◐ ○ ✖ ◌ ⚠) and a count per status in its header; without
the definition route it chains the reported steps and says `linear (no
definition)`, and a graph too wide for the card says so and leaves the step
list. Three ways open it: Enter on an execution row in Work;
`/workflow graph [id|latest]` (no id or `latest`: the most recent running
execution, else the most recent of any status; an id may be a unique prefix
of a tracked one, an untracked id is read once from UMRS; no executions →
a message pointing to `/work`; an unknown id is named back); and Alt+W,
which opens the most recent running execution in the Work pane's scope (a
notice when none runs, and a notice instead of the overlay while a dialog
— help, palette, a tool approval, another card — is open). An explicit id
opens a run outside the scope too. A read that fails for another reason
than "not found" (timeout, offline) says `could not read workflow execution`.
`src/lib/workflow-graph-open.ts` is the one opener;
`tests/workflow-graph-verb.test.tsx` pins the verb, the badge and the key.

`src/lib/workflow-params.ts`, `src/lib/dag-layout.ts` and
`src/lib/workflow-card-rows.ts` are pure, with tests:
`tests/workflow-params.test.ts`, `tests/dag-layout.test.ts` (it traces the
drawn lines of random DAGs back to their edges),
`tests/workflow-def-overlay.test.tsx` and
`tests/workflow-trigger-draft.test.tsx`.

**Following a team live (2026-09-27).** While a teamup session runs, the
console keeps four surfaces current without any manual refresh:

1. the Work-pane row shows the active agent (`… · pg ⣾`) and its latest line,
2. the team overlay (Enter on the row) shows a live roster — role, model
   (when set), status, messages sent, last output, **progress bar for the
   whole run in the header + a running-frame on the working agent** (progress
   UI, 2026-09-27) — and opening an agent (click, or `j`/`k` ↵ select + Enter;
   verbs now ride the global keymap so they work over any composer, 2026-09-27)
   shows the **verbatim prompt it runs under** (source named: default role
   prompt or spec override); `m` asks the **root agent to write every role's
   prompt from THIS project** (prompt tailoring, B3), roles it skips keep
   their crew pack's baselines (prompt header names the crew, 2026-09-28),
3. a one-row chat notice (`team <name> — iter i/N · <tail>`) updates in
   place, Enter expands the recent event lines,
4. a `team <name> <role>⣾` chip rides the status bar until the run ends.

The push is the bridge `team_event` line during `teamup.run` (protocol v1
additive — older companions ignore it); `teamup.status` rows carry
`prompt`/`prompt_source`/`model`.

**Following a team plan live (ADR-092, 2026-10-11).** A team plan run
(started by the agent with `team_plan_run`, or by you with
`/teamup on plan:<template>`) streams `team_event` lines keyed by `run_id`
and by agent id (`<step>[<item>]#<replica>`): `plan_started`, `phase`,
`agent_started`, `agent_round` (tokens), `agent_tool`, `agent_result` /
`agent_failed` / `agent_cancelled`, `gate_requested` / `gate_answered`,
`plan_finished`. The console keeps one notice per run (`plan <name> — n ok ·
m null · tokens`) whose expanded rows are the per-agent results (✓ answer
preview, ✖ null with its reason, ⊘ cancelled), and a Work-pane row per run.
Starting a plan always shows its approval card, in every permission mode
(MD-5); the card is never remembered for the session and no project rule
answers it, and approving it grants the card's safe tools to that run's
agents only (MD-6). Every approval card names the plan agent or team role
that asks. Stopping the chat turn (Esc) that started a plan cancels the plan
too; its agents in flight are journalled as cancelled. A running UMRS workflow execution is
re-polled (~4 s) until it is terminal, so an agent-triggered cluster run
updates in the workflow graph without another Enter.

---

## 4. Keyboard

Global (from `src/keymap/index.ts`; letters never fire while you type or
while an overlay is open):

| Key | Action |
|---|---|
| `Ctrl+P` | Command palette (navigation, backend actions, every slash verb) |
| `Ctrl+.` or `Ctrl+/`, or typing `/` in chat | Palette pre-seeded with `/` (the slash surface) |
| `F12` / `Ctrl+Shift+H` / `/help` | Help overlay: chord legend, the full live slash catalog, the live keymap |
| `Esc` | Close the topmost layer, one at a time (the ladder in [Key ownership](#key-ownership-developers)): a modal (palette, picker, help, quit, approval card, agent question) closes alone — the running turn, its queue and the docked pane stay *(2026-10-21; before, a modal's Esc also interrupted the turn and closed the pane)*; then the detail pane; in chat: interrupt a streaming reply, else clear the draft |
| `Ctrl+Q` (twice within 3 s) or `/quit` | Quit |
| `y` / `Enter` · `a` · `n` · `r`/`Esc` | On the tool-approval card (keys the card offers): allow this call · always this session · reject with feedback · reject (ADR-104, 2026-10-31: `y` no longer auto-approves later calls; amendment 1, 2026-11-03: `p` "always for this project" removed) |
| `Ctrl+G` | Enter the transcript cursor on the latest message (`Shift+Tab` and `Ctrl+↑` do the same from the composer) |
| `Ctrl+Y` | Copy the active mouse-drag selection (OSC 52) |
| `Ctrl+Alt+M` | Toggle mouse reporting (native terminal selection on/off) |
| `Shift+Tab` (empty draft) | Rotate interaction mode chat → plan → autopilot (live backend only); note the composer also maps `Shift+Tab` to the transcript cursor, so `/mode` is the unambiguous switch |
| `Ctrl+PgUp` / `Ctrl+PgDn` (`Ctrl+Shift+[` / `]`) | Cycle Activity Console tabs (opens it if closed) |
| `F9` / `Ctrl+Shift+Y` | Open / close the Logs pane (`/logs`, the Activity Console) *(row added 2026-10-21; the keys predate it)* |
| `Ctrl+T` | Open the Logs pane on its Todos tab (again closes it) |
| `Ctrl+N` | New chat tab (fresh companion session, own bridge process) |
| `Ctrl+Alt+W` | Close the active chat tab (right neighbour activates; the last tab refuses) |
| `Ctrl+Shift+T` | Reopen the most recently closed tab (session resumes from the journal) |
| `Alt+→` / `Alt+←` | Next / previous chat tab (wraps) |
| `Alt+W` | Open the live DAG overlay of the most recent running UMRS workflow execution (the status-bar `▶` badge's run); a notice when none runs or while a dialog is open *(2026-10-24; Ctrl+Alt+W stays tab close)* |

Composer (`src/components/chat.tsx`):

| Key | Action |
|---|---|
| `Enter` | Send; on an empty draft, resolve the latest error card or fold/unfold the latest tool roll-up |
| `↑` / `↓` | Prompt history (`~/.config/r4/prompt-history.jsonl`, 50 entries, shared across r4 surfaces) |
| `Shift+Enter` / `Alt+Enter` / `Ctrl+Enter` / `Ctrl+J` | New line in the draft (2026-10-02: kitty keyboard protocol enabled so modified-Enter keys arrive distinctly; on terminals without kitty support use `Ctrl+J`. 2026-10-19 debug wave: kitty `CSI 13;2u` appends a line through the full stack, while a legacy shift-press submits (byte-identical to Enter; tmux/screen swallow the negotiation by default); the SAME wave fixed two claim bugs — legacy Alt+Enter (`ESC`+`CR`, decoded as return+meta only) used to SUBMIT, and `Ctrl+J` (decoded as the `linefeed` key, never `j`+ctrl) used to do nothing; both append now. The composer footer ADAPTS to the negotiated capabilities: `Shift/Alt+Enter` only with kitty, else `Alt+Enter/Ctrl+J`) |
| `Ctrl+E` | Edit the draft in `$VISUAL` / `$EDITOR` |
| Paste | A paste of 3+ lines or 150+ characters becomes a `[Pasted text #N ~L lines]` chip in the draft, expanded to the pasted text at send; one Backspace removes the whole chip. On the title screen a multi-line paste moves to the composer with its line breaks *(2026-10-21)* |
| `!cmd` | Run in the local shell; full output shown in the transcript AND relayed into the model as a turn so the agent responds (queues behind a running turn; never names the tab) |
| `@` | File-mention popup over the workspace (↑↓, `Tab`/`Enter` inserts) |
| `l` (empty draft) | Open Logs for the latest error card |
| `End` | Jump back to the latest message when scrolled up |
| Plan card awaiting confirmation | `a` accept → autopilot · `m` accept → chat (manual approval) · `n` / `Esc` keep planning · `e` edit |

Transcript cursor: `↑`/`↓` move, `c` copy, on your own rows `r` resend,
`e` edit & resend, `f` fork the session, on a cited reply `k` opens the
first citation in Knowledge, `Esc` leaves.

Overrides: `~/.config/r4/v3-keymap.json` (`$R4_CONFIG_HOME`), a flat
`{ "cmd.id": "key" }` map, additive and ignored when invalid. There are no
default pane-jump keys (F-keys, Ctrl+digit and the comma chords were
retired); power users can bind `space.goto.<pane>` there.

**Pane focus note (revised 2026-10-03).** The 2026-09-22 claim that nothing
sets `paneFocus` is stale: panes open focused (`actions.openPane`). The
Terminal view is the exception — it opens unfocused and its focus moves by
mouse click alone. What is still missing for the other views is a GENERIC
focus-in/out binding; Esc remains the universal way back to chat. Panes
with their own key trap — Tasks, Work, Infrastructure, Knowledge, Settings,
Loop, the catalog panel — take keys as listed above; Sessions and Code-view
letter verbs and the Research letter verbs work over the global keymap
while their pane is focused (any draft in the composer blocks them, by
`idle` gating).

### Key ownership (developers)

**Written 2026-10-18 (plan H-6, platform-hardening-onboarding-plan-2026-10-17.md).**
Who gets a key, and how a component takes one. Code:
[`src/keymap/key-claims.ts`](../../packages/r4-tui-v3/src/keymap/key-claims.ts)
(the registry), [`src/keymap/store-claims.ts`](../../packages/r4-tui-v3/src/keymap/store-claims.ts)
(store-owned layers), [`src/keymap/index.ts`](../../packages/r4-tui-v3/src/keymap/index.ts)
(bindings and handlers).

**Why a model is needed.** The global keymap is `prependListener`ed on
`renderer.keyInput`, so it runs *before* the focused renderable (a
`<KeyTrap>`, the composer input). A keymap handler that does not return
`false` consumes the key (`preventDefault`) and the focused component never
sees it. `preventDefault` inside a focused component cannot stop a global
binding. And `<KeyTrap>` reads `focused` once at creation: to hand keys back
after an overlay closes, remount the trap (`<Show when={!overlayOpen}>`).

**Precedence**, highest first:

1. **Modal overlay trap.** An open overlay (palette, pickers, ask-user,
   help, quit, list, start menus, approval card) owns typed letters, Tab and
   its trap's list chords (Ctrl+N / Ctrl+P / Ctrl+B). Its Esc runs the Esc
   ladder below.
2. **Claims (Esc / Tab / search).** Component-local key state: the
   composer's Ctrl+R search (letters, Tab, Ctrl+G, Esc), its @-mention popup
   (Tab), a non-empty draft (Esc; letters while no pane holds the keys), the
   approval card's feedback stage (Esc, typed text), the remote-source
   picker's pending push confirm (Esc), the catalog search (Esc), the
   infrastructure lists (Tab), the Settings panels (Tab, 2026-10-27), a
   focused verify input (letters, Tab, Esc).
3. **Pane-focused bindings.** Pane letters (`space` field + `whenPaneFocused`)
   fire only after Tab moved the keys into the docked pane, and only while
   no `letters` claim holds.
4. **Global bindings.** Modifier chords (Ctrl+P, Ctrl+O, Ctrl+T, …),
   Esc (the ladder) and Tab (chat ↔ pane focus).
5. **Composer.** Every key nobody above consumed: typing, Enter (send),
   ↑/↓ history, Ctrl+R search, Ctrl+S stash, Esc Esc rewind, Ctrl+Enter
   (send queued now). These composer keys are not keymap bindings; the help
   legend lists them and the binding lint test allows exactly these.

**The handler contract.** Every keymap command is a `KeyHandler`
(`() => boolean`): `true` consumes the key, `false` yields it, and OpenTUI
then tries the next binding for that key and finally the focused
renderable. A handler never returns `undefined` (the keymap would treat it
as consumed).

**Claims, not component state.** Handlers never read a component's state or
a list of overlay flags. They ask the registry: `keyClaimed(key)`,
`keyClaimants(key)` (owners, highest rank first), `runClaim(key, name)`
(hand the key to the highest-ranked active claimant; a claimant without
`onKey` yields). Two kinds of owner register claims:

- State in the workspace store (an overlay's open flag) → one row in
  `store-claims.ts`, the table below, registered once on import.
- State in a component (signals) → the component calls
  `claimKeys(owner, keys, active, { rank, onKey })` and releases it in
  `onCleanup`.

Within a rank the newest claimant comes first (the layer mounted last is on
top). The registry is reactive: the App effect that feeds the keymap's
`app.idle` and `claim.*` data re-runs when a claim comes, goes, or changes.

Claim keys:

| Claim key | Meaning | Claimed by |
|---|---|---|
| `escape` | Esc ladder (ranked) | the ladder below |
| `tab` | chat ↔ pane toggle yields | modal overlays, pickers, splash, verify input, composer search, @-mention popup, infrastructure lists, Settings panels (`settings-panels`, 2026-10-27) |
| `letters` | pane / overlay letters yield; `app.idle = !keyClaimed("letters")` | modal overlays (not the rewind menu, not the tour), pickers, splash, verify input, composer search, `composer-draft` (a draft while no pane holds the keys) |
| `ctrl+n` `ctrl+p` `ctrl+b` | a trap's list navigation | palette, slash picker, ask-user, rewind menu, start-actions menu, list overlay, venue and remote-source pickers, tour |
| `ctrl+g` | Ctrl+G cancels the search, not the transcript cursor | composer search |
| `text` | typed text and Enter belong to another input; the composer swallows them | approval feedback stage |
| `approval` | the y / a / n / Enter verbs (bindings `claim: "approval"`) | approval card (`onKey` runs the verb; yields in the feedback stage) |
| `team-overlay`, `exec-overlay` | detail-overlay verbs (bindings `claim: …`) | team / workflow-execution detail rows |

The Esc ladder (`overlayCloseSweep()` = `runClaim("escape")`):

| Rank | Owner | Esc does |
|---|---|---|
| 100 | `btw-overlay` | closes /btw (never touches the running turn) |
| 100 | `citation-preview` | closes the citation passage preview (K1.4; never touches the running turn) |
| 95 | `codeview-revert-arm` | cancels the armed revert, the pane stays |
| 90 | `splash` | skips to chat; yields while an overlay is drawn over it; a typed draft carries into the composer *(2026-10-21)* |
| 85 | `approval-feedback` (component) | back to the approval card |
| 80 | `tour` | closes the tour, the pane stays |
| 79 | `remote-source-picker-confirm` (component) | yields: the picker's trap clears the confirm |
| 78 / 77 / 76 | `remote-source-picker` / `web-access` / `venue-picker` | closes the picker or the web-access question (a held first message goes back into the composer; a new session is not started), the pane stays *(`web-access` 2026-10-23, ADR-099)* |
| 70 | team / agent / endpoint / exec detail, workflow card | closes the detail overlays, the pane stays |
| 60 | modal overlays | `closeModalOverlays()`: every modal layer (approval rejected, agent question declined); the running turn, its queue and the pane stay *(2026-10-21; it was `closeAllOverlays()`, which also interrupted the turn and closed the pane)* |
| 50 | `verify-input`, `composer`, `science-catalog` | yield: the component's own Esc runs |
| 10 | `pane` | closes the docked pane |

Binding fields: `space` (the docked pane view), `idle` (no `letters` claim)
and `claim` (some owner holds that claim key). The three 2026-10-17 guard
fields `approvalOpen`, `teamOverlay`, `execOverlay` and the unused
`activityOpen` are gone.

**Adding a key owner.** If its open state is a store field, add a row to
`store-claims.ts` at the right rank. If it is component state, call
`claimKeys` from the component. Never import component state into
`keymap/index.ts`. Then add a cell to the matrix test.

**Tests.** `tests/key-claims.test.ts` (registry: rank, ties, release,
yield, reactivity); `tests/key-ownership-matrix.test.tsx` (full `<App/>`,
40 cells: Esc / Tab / Enter / letters per layer); the binding lint in
`tests/tui-r2-keymap-bindings.test.tsx` (no bare uppercase key, every
binding's command exists, every handler returns a boolean, every help-legend
key is bound or a composer key, the help overlay lists every binding).

**Not claims yet (2026-10-18).** Global chords read the composer's
published store fields directly: Ctrl+C / Ctrl+D / Ctrl+B read
`workspace.chatDraft` and `chatStreaming`, Shift+Tab (`mode.rotate`) defers
to the research pane by `paneView`. The research pane helpers in
`src/lib/research-keyboard.ts` read `workspace.verifyInputFocused` beside
`letterShortcutsBlocked()`. These are store fields, not component state;
moving them to claims is listed in the plan's H-6 row.

---

## 5. Command palette and slash commands

`Ctrl+P` opens one fuzzy list: registry rows (Open pane: …, Provision model
endpoint, Stop endpoint, Cancel job, Retry job, Show job logs, Research tab:
Ops/Strategy/Explain, Help overlay, Clear chat, Quit) followed by every slash
verb (`slash/<category>`). A leading `!` reveals hidden rows (`/quit`).
Typing a full line such as `mode plan` or `login <token>` and pressing Enter
dispatches it verbatim; ↑↓, PgUp/PgDn, Home/End move, Esc closes. Typing `/`
in chat opens the same palette; a pasted line with arguments stays in the
composer and dispatches on Enter.

The catalog (`src/commands/slash.ts`) merges three sources: console-local
verbs, the Companion registry served by the bridge (`list_slash_commands`,
from `r4/src/r4/tui/commands.py`), and the Bun-only `/project` family.
Ownership (local vs forwarded, aliases, intercept stage, verbs that stay
local) is declared once in `src/commands/catalog.manifest.json`; the bridge
reads a copy of it and `scripts/regen-slash-fixture.py --check` keeps both
current (plan H-5, 2026-10-18). `F12`
prints the live list with help text; the tables below name the verbs that
exist today.

**Console-local**

| Command | What it does |
|---|---|
| `/help` (`/h`, `/?`), `/palette` (`/commands`), `/clear`, `/quit` (`/exit`) | Help overlay · palette · clear transcript · quit |
| `/overview` `/research` `/tasks` `/work` (`/workflows`) `/knowledge` `/infra` `/loop` `/logs` `/sessions` `/catalog` `/codeview` `/term` (`/terminal`) | Open that pane |
| `/settings [matrix \| rules [rm <n> \| clear] \| ambient_cards on\|off \| bell on\|off]` | Policy matrix · project "always allow" rules · ambient event cards · completion bell |
| `/theme [name \| list]` | Theme picker with live preview (Esc restores), or apply by name |
| `/tour [map \| n]` (`/guide`, `/welcome`) | Guided walkthrough; `map` prints the function index |
| `/mode [chat \| plan \| autopilot]`, `/mode write-plan on\|off` | Interaction mode (badge flips at Enter); require writes to be planned first |
| `/code on\|off` (`/coding`) | Toggle the builtin agentic code skill |
| `/acquisition [open \| off \| extend <graph>[,…]]` (`/acquire`) | Session knowledge-write mode (see §8) |
| `/models` (`/model`) · `/endpoint up\|down\|status\|list` · `/up` · `/down` | Model picker · endpoint lifecycle (bare `/endpoint` opens Work) |
| `/decision-model [on [model] \| off \| status]` (`/decision`) | Decision model (ADR-076): `decision_evaluate` — fast choice / score / yes-no judgments with `Bespoke-Nimble-9B`. **On by default since 2026-10-03** wherever the platform serves it; `off` is saved in the profile and applies from the next message. While on, the session also uses it to read unclear replies to a plan, to ask before an auto-approved risky tool call that looks off-task, and to drop false lessons. It only ever adds caution (2026-10-02) |
| `/skills` (`/skill`) · `/mcp` · `/changes` · `/governance` (`/gov`) · `/connect` | Selectable overlays: skills, MCP inventory, session file changes (Enter diff, r/R revert), governance, connection menu. `/connect`'s login / add-public-endpoint rows open a guided Q&A form (one masked prompt per value, e.g. the JWT) that builds and runs the `/login` / `/source add` line for you (2026-10-19) |
| `/source` | Active-source overlay: Enter switches (`r4 source use`), `h` refresh, `p` push / `u` unpush a sealed source. Health glyphs ✓ ok / ✗ down / ? unknown; platform sources probe `/ready`, and a public source with a key is not probed (2026-10-15) |
| `/source add <name> --url <url> [--api-key <k> \| --api-key-env <VAR>] [--provider p] [--description t] [--allow-insecure-http]` | Register a public source: the key goes to r4 on stdin, the echo is masked and the line never enters prompt history (2026-10-15) |
| `/login <token>` | Authenticate with a JWT or onboarding token — it goes to `r4 auth login --api-key-stdin` on stdin, and the echo is masked (2026-10-15) |
| `/health [authz]` · `/usage` · `/quota` · `/grants` · `/groups [id]` | Health summary (+ authz audit) · owner-scoped usage rows · resource gauges · grants · groups |
| `/files upload <path> [key]` · `/files download <key> <dest>` | Gateway file transfer (project namespace; 2026-09-27) |
| `/explain <error.id>` | Error-code lookup: cause + remediation from the registry (2026-09-27) |
| `/teamup tailor [id]` | Root agent (PM/first role) writes every role's prompt from THIS project (B3, 2026-09-27) |
| `/teamup on [crew]` · `/teamup off` | Arm one-shot: next chat message starts a team on that text (2026-09-27). `hpc [width]` arms the HPC optimisation session (PM·SE·PG·CD + hardware). Every other crew (default `research`; `code`, `paper-writing`, `kakenhi-report`, `peer-review`, or an alias such as `paper`) is a plan template since ADR-092 slice 4: it arms that template's plan exactly as `/teamup on plan:<template>`, and its card asks you before anything runs. `/teamup crews` names each crew's template |
| `/teamup on plan:<template> [name=value …]` · `/teamup plans` | Arm one-shot team **plan** (ADR-092, 2026-10-11): the template is checked first; your next chat message becomes its goal (the template's first required text parameter), its approval card asks you, and the run streams live into Work. `/teamup plans` lists the templates and what each needs |
| `/sweep [list\|preview <name>]` | HPC sweep templates inventory + dry render (2026-09-27) |
| `/evidence [status]` | Evidence plane rows: claims/retractions, caller-scoped (2026-09-27) |
| `/job <job-id>` · `/traces [<job-id> \| get <trace-id>]` · `/provenance <job-id> [lineage]` · `/loops [list \| status <id> \| detail <id> \| stop\|pause\|resume <id>]` · `/checkpoint save\|list` | Job attempt timeline · execution traces · provenance · research loops (detail = recent iterations — auto-follows a live loop, 2026-09-27) · manual checkpoints |
| `/rag <question> [top_k=<n>] [project=<id>]` · `/admin …` | RAG corpus query · operator rows |
| `/undo` · `/compact` | Restore the last prompt into the draft · compress history |
| `/teamup …` (`/team`, `/teams`) | Multi-agent team sessions: `create`, `list`, `status`, `run`, `pause`, `resume`, `cancel`, `logs`, `report`, `hardware`, `engines`, `crews` (rows live in Work) |
| `/sessions [new \| resume <id> \| rename <id> <name> \| pin\|unpin <id> \| archive <id> \| export [id] \| turns <sid> [<turn>]]` | Session verbs (bare opens the pane; `resume` opens the session in its own chat tab) |
| `/tab [list \| new \| close [n] \| next \| prev \| reopen \| <n>]` | Chat tabs: parallel session strip (bare lists) |
| `/term [new \| kill \| share [lines]]` (`/terminal`) | Terminal pane: open/reattach (bare) · fresh shell · kill shell + close · relay buffer tail to the agent (2026-10-03) |
| `/venue [status \| start \| stop [<id>] \| sources \| approvals]` | Remote session runtime (see §7) |
| `/web` | The agent's web access for this session: Off, Allowlist (domains) or Public (see §7; 2026-10-23, ADR-099) |
| `/search-server [status\|up\|down]` | The local SearXNG behind `web_search`: show it, start it (the first start downloads about 200 MB; each step lands as a notice) or stop it (2026-11-01, ADR-102) |
| `/sandbox [on \| off \| status \| default on\|off]` | The local tool sandbox for this session (see §7; 2026-10-30, ADR-101): `on`/`off` apply from the agent's next command and are refused while an answer streams (wait for it, or stop it, then run `/sandbox` again); `status` shows backend, write roots and network; `default on\|off` also saves it as the profile default (same as `r4 config sandbox on\|off`). A frontend session answers that it already runs in the server's sandbox |
| `/project <list \| current \| create \| switch \| import \| init \| verify \| readiness \| plan \| run \| cancel \| status \| findings \| ingest \| session>` | Project lifecycle via typed `project_*` RPCs |
| `/workflow` | Bare: opens Work; `graph [id\|latest]` opens the live execution DAG overlay (2026-10-24; local, see Work above); `list \| trigger \| upload \| delete \| status \| cancel` ride the bridge (`delete`, 2026-10-19: the typed name is the confirmation; owner/admin server-side) |

**Companion registry verbs** (dispatched over the bridge; a representative
set — `F12` lists them all): `/trust` (`/yolo`, = `/mode autopilot`),
`/plan`, `/new`, `/export [path.md]`, `/save path.md` (a file;
`/save [n] [mine] [--vouch]` keeps a turn in project knowledge, K1.6),
`/share`, `/context`, `/cost`, `/history`, `/retry`, `/system`, `/config`,
`/tools`, `/research list|status|start|stop|session`,
`/rag query|upload|docs` (`/rag upload <file>` uploads into the project; `/rag docs delete <id> --yes` deletes a document, and without `--yes` says what it would delete; K1.3), `/dashboard` (`/status`, `/refresh`), `/probe`,
`/template`, `/lessons`, `/progress`, `/decision-model on [model]|off|status`
(ADR-076; `decision_evaluate` with `Bespoke-Nimble-9B`, on by default since 2026-10-03).

---

## 6. Interaction modes and the permission ladder

*Rewritten 2026-10-31 for [ADR-104](../adr/104-one-mode-one-permission-decision.md)
(one mode, one decision, approvals answered by the companion). Before: a
second "permission mode" (`ask_always|auto_safe|deny|trust`, Alt+M,
`/approve`, `/allow-all`, a sticky `permission_override`) sat beside the
mode, and the console answered some approvals itself (ask-once, its own
project rules).*

**One mode** — `chat`, `plan`, `autopilot`; switch with `Shift+Tab` (empty
draft), `/mode <m>`, or `/trust` (alias `/yolo`, = `/mode autopilot`);
`r4 --allow-all-tools` launches in autopilot. The status line shows
`mode:<m>`; it reads `autopilot(policy)` when your organisation has turned on
its autopilot allowlist (`R4_AUTOPILOT_SERVER_POLICY=1`,
[ADR-035](../adr/035-enforcement-point-matrix.md)), plain `autopilot`
otherwise. *(2026-11-03, ADR-104 amendment 1: signed in, autopilot used to
consult a server allowlist nobody seeds, so `autopilot(server)` asked for
every tool, read-only ones included.)* `/settings matrix` prints:

| Mode | What runs without a card |
|---|---|
| chat (default) | read-only tools; every other tool asks |
| plan | read-only tools only, other calls are refused; the first reply is a plan, then the plan card: `a` accept → **autopilot** and run, `m` accept → **chat** and run, `n`/`Esc` keep planning, `e` edit. Typing "yes" is `m`. A stopped run returns to plan with the plan pending |
| autopilot | every local non-deletion permission prompt is auto-answered: taint, credential/force-push fences, knowledge/research decision prompts, your `ask` rules, organisation-policy prompts, plan gates and team-plan cards. The original decision appears as an `[autopilot] …` line, never as a fabricated human grant. `ask_user` also chooses a reasonable answer, states its assumption and continues. Recognised user-file deletion/worktree removal still asks (below); explicit denies and backend/sandbox refusals remain |

**What asks or refuses**, in this order (the ladder in
`r4/src/r4/companion/permissions.py`, `PermissionManager.decide`): a `deny`
rule refuses in every mode. The never-auto, taint, credential/force-push,
user-`ask`, protected-path and organisation-policy rungs still classify the
call. In chat/plan those classifications ask; in autopilot
`PermissionManager.resolve` turns a non-deletion ask into an audited allow
(*2026-11-09, amendment 3*). Local automatic answers do not manufacture a
human actor, card grant or gate provenance: UMRS knowledge/loop/workflow
policy, compute thresholds, HPC signing, tool availability and sandbox
restrictions still decide whether the action may execute. A refused tool
disappears only while its deny is tool-wide; your "no" on one call never
hides it.

**Autopilot deletion always asks** (*2026-11-09, amendment 3*). Recognised
user-file deletion (`apply_patch` Delete File or a rename's old path,
`rm`/`rmdir`/`unlink`/`shred`/`git rm`, `git clean`/`find -delete`/runtime
paths, Python delete calls), `worktree_exit(action="remove")`, and a
merge-back whose structured change set contains deletions require your yes
even when a backup would be possible. Non-deleting merge-backs run. R4's
R4-owned temporary cleanup and backend resource deletion are outside this
rule; textual recognition is best-effort, not a guarantee for arbitrary code
or MCP side effects. In other automatic modes, amendment 2 still copies
recoverable targets into `~/.local/share/r4/trash/<time>-<id>/` before the
call or asks when it cannot.

**Approval card** (inline, bordered, always fully shown). The companion
says which answers the card offers; the console never answers by itself:
`y`/`Enter` allow this call; `a` always, for the rest of this session (the
companion keeps it in memory); `n` reject with feedback (Enter
queues your text as the next draft); `r`/`Esc` reject that call — the turn
keeps running and the agent is told you said no. A **fenced** or never-auto
card offers only `y` and `n`/`r`. Several asks at once (parallel agents)
queue; the next card appears when you answer. Esc on another overlay (help,
palette) closes that overlay and leaves the card waiting. For remote
sessions the same card answers through `session.venue_decide` (once /
always / reject). *(2026-11-03, ADR-104 amendment 1: `p` "always for this
project" is gone — one keypress wrote a durable, argument-blind allow rule
for every later session; put standing rules in a rules file on purpose.)*

**Rules** (G14): `~/.config/r4/permissions.json` (yours: allow/ask/deny),
`~/.config/r4/projects/<id>/permissions.json` (yours, per project) and `<workspace>/.r4/permissions.json` (checked in: ask/deny only;
an `allow` there is ignored). Entries are `"tool"` (wildcards: `"mcp.*"`) or
`"tool(prefix:text)"`. `/settings rules` lists every rule with its source;
`/settings rules rm <n>` removes one of this project's rules.

---

## 7. Sessions: local and remote

**Lifecycle repair note (2026-09-28, checkout changes):** resuming a frontend
session refreshes the cached transcript from the journal; an unavailable
journal produces an explicit cached-copy notice. Remote queued followups
are consumed only by the worker, never popped and resubmitted by the TUI.
Escape stops watching without clearing queued remote work. Completion
accepts reply adoption and the idle heartbeat on different polls.

Team controls share an in-process run claim across the TUI and agent-tool
entry points. Pause/cancel stop subsequent roles after the current provider
call returns; resume permits another explicit iteration. Direct TUI teams
are still local bridge runs, even while chat uses a frontend venue. This is
not a cross-process lock or remote durable-team guarantee. Validation and
remaining live checks are in the function sweep.

Sessions persist server-side and are listed in the Sessions pane. The venue
is chosen when a session starts (`n` in the pane or `/sessions new`):

| Option | Meaning |
|---|---|
| `[l]` Local | runs on this machine; dies when r4 exits; resumable here only |
| `[f]` Server (frontend) | runs on the platform server in the ADR-046 sandbox worker; survives disconnect; openable after login. Disabled with the server's reason while the session journal is unreachable (2 s probe) |
| `[c]` Cluster | disabled: loop runtime only today; companion-session cluster venue lands separately |

**Web access (2026-10-23, [ADR-099](../adr/099-session-policy-and-quotas.md)
D-2).** After the venue, the console asks for the agent's web access in this
session: **Off** (`none`), **Allowlist** (type the domains; `.a.org` includes
its subdomains) or **Public**. A Server session's question offers only what
the session policy allows (`session.venue_policy`; a domain cap removes
Public). The current or remembered choice is preselected, never applied
without the question. A session that has none yet (a fresh console boot, a
session saved before RS-1) asks on its first submit and holds the message
until the answer; Esc puts it back in the composer. `/web` shows and changes
the choice: locally it applies to the agent's next web call; a running remote
session answers `venue.restart_required`. `r4 --web off|allowlist|public
[--web-domain D]` sets it for sessions that have no choice (new and resumed),
without the question. `R4_WEB_ACCESS=MODE[:d1,d2]` in the environment only
preselects the question here (2026-10-23: it used to answer it). Locally the choice gates `web_fetch`
(redirects checked hop by hop) and `web_search` (Public only); shell, Python
and MCP tools use your network after your approval, unless the local tool
sandbox is on (below; *2026-10-30*). A web tool the choice
allows is offered whatever skill is active (2026-10-30). `web_search` also
needs a SearXNG instance: `r4 search-server up` runs one on 127.0.0.1, or
`R4_WEB_SEARCH_URL` names one (2026-10-30, [ADR-102](../adr/102-local-searxng-for-web-search.md)).
Choosing Public without either asks "Start a local one now?" when the
session is local and podman or docker is installed; Enter starts it and the
steps land as notices, Esc leaves it. Otherwise a notice says so. `/search-server`
shows, starts and stops it any time (2026-11-01, ADR-102 Amendment 1).

**Local tool sandbox (2026-10-30, [ADR-101](../adr/101-local-tool-sandbox.md)).**
Off by default. When a local session opens or resumes with it off, one
notice row says so: "Sandbox off: shell, Python and MCP servers run with
your full permissions. Turn it on with /sandbox on (this session) or r4
--sandbox on (at launch)." Enter expands the row into what turning it on
confines. In `r4 -p` and the plain REPL, which have no `/sandbox` command,
the line names what they have instead: `r4 --sandbox on`, `R4_SANDBOX=on`
or `r4 config sandbox on`. When this machine has no usable backend, the row says why and how
to fix it instead (for example: install bubblewrap, or the Ubuntu 24.04
AppArmor profile), and the chip reads `sbx:n/a`. `/sandbox on` runs a short
self-test first and is refused if it fails; the chip then reads `sbx:on`.

What it does, while on (it confines the processes the agent starts, not r4
itself):
- Every process the agent starts (`shell_exec`, `python_exec`, compile, run
  and test tools, language servers) runs under bubblewrap (Linux) or
  Seatbelt (macOS), and so do your local stdio MCP servers and hatch
  runtimes.
- Writes land only in the workspace, a per-session temp directory (on Linux
  `/tmp` inside is that directory) and your `extra_write_paths`. Inside the
  workspace, `.git` hooks and config, `.r4/config.toml`, `.r4/local.toml`,
  `.r4/mcp.json`, `.r4/mcp-servers.json` and `.github/mcp.json` stay
  read-only, and so does r4's own installation.
- A fixed list cannot be read: `~/.config/r4`, `~/.ssh`, `~/.gnupg`,
  `~/.aws`, `~/.azure`, `~/.config/gcloud`, `~/.kube`, `~/.docker`,
  `~/.config/gh`, `~/.netrc`, `~/.git-credentials`, `~/.pypirc`,
  `~/.npmrc`, the X11 auth files, the macOS keychains, wherever
  `KUBECONFIG`, `GH_CONFIG_DIR`, `DOCKER_CONFIG`,
  `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, `CLOUDSDK_CONFIG` or
  `GNUPGHOME` point, and your `sandbox_policy.extra_deny_read_paths`.
  Environment names containing `TOKEN`, `SECRET` or `PASSWORD`, names
  ending in `_API_KEY` and the AWS key names are removed (`R4_GATEWAY_URL`
  and `R4_GATEWAY_TOKEN` stay, so hatch packages keep working).
- The network is closed except through r4's own loopback proxy, which
  follows `/web`: Off gives no network at all, Allowlist only the listed
  domains, Public any public host. Private, LAN and cloud-metadata
  addresses are always refused, and only ports 80 and 443 are open.
  Local MCP servers take their own `sandbox.web` entry in
  `~/.config/r4/mcp-servers.json` (default Off) instead of `/web`, and
  hatch runtimes keep the host network (they must accept loopback calls).

What it does not do:
- It does not approve anything for you: the permission ladder still asks.
- It does not confine r4 itself. The built-in file tools (`file_read`,
  `file_write`, `file_edit`, `apply_patch`, grep and glob search) run inside
  r4 and are limited by the permission ladder only, not by the sandbox;
  hooks also run outside it.
- Project hooks (`.r4/hooks.json`, loaded when
  `R4_COMPANION_PROJECT_HOOKS=1`) run inside the sandbox while it is on, and
  an existing `.r4/hooks.json` is read-only (2026-10-30). Your own hooks in
  `~/.config/r4/hooks.json` run outside it; a script they name by a
  workspace path stays writable by the agent.
- A background agent or team plan keeps the sandbox setting of the session
  that started it: switching sessions, or `/sandbox off` in another
  session, does not change it (2026-10-30).
- It does not hide secrets outside the list above, for example
  `~/.cache/huggingface/token`, `.env` files in the workspace, the file
  `GOOGLE_APPLICATION_CREDENTIALS` names, the rest of your home directory,
  or an environment name that matches none of the rules (a `DATABASE_URL`
  with a password in it). Add such paths to
  `sandbox_policy.extra_deny_read_paths` and unset such names.
- It does not stop what already runs: processes started before `/sandbox
  on` keep running unconfined (the switch reports them as `still_running`), and a process
  started under Off stays offline after `/web` opens it; restart it.
- It does not reach git over SSH, LAN or cluster hosts, or a corporate
  upstream proxy; turn it off for that work. An `r4` you start from a
  sandboxed shell cannot read your r4 config and runs unauthenticated.
- `/sandbox off` in one session does not loosen local MCP servers that
  another session (or your profile default) put under the sandbox; only
  `/sandbox default off` or `r4 config sandbox off` does. A resumed session
  that was on stays on even if launched with `r4 --sandbox off`.
- It does not apply to Server sessions: they already run in the ADR-046
  sandbox and show no chip; `/sandbox` answers that the session already
  runs in the server's sandbox, and `/sandbox on|off` changes nothing.
- macOS (Seatbelt) has not yet been run live; there is no seccomp filter.

Choosing Server opens the **remote source picker** after that question:
rows are your public sources that carry an API key, with the server badge
(`pushed (…)`, `stale`, `tombstoned`, `not pushed`, `server unreachable`).
The footer shows the chosen web access (`web: …`). `Enter` starts with the
selected source and that choice (a not-yet-pushed source asks once, then
pushes through the bridge — the key never crosses the TUI), `p` push, `u`
unpush, `r` refresh, `Esc` closes. (Until 2026-10-23 `w` cycled a
preselected `public` here.)
The start answers `provisioning`; the console fast-polls (2 s, up to 5 min)
and reports `running`, `failed` (with the server's reason) or `stopped`.

While a frontend session runs: `send_message` queues the turn on the server,
the console polls `session.venue_sync`, renders the worker's partial text as
a streaming bubble and adopts the server snapshot when the turn sequence
advances (server wins for `frontend`). **Live tool visibility (2026-09-27):**
the worker also streams bounded tool-activity lines (`▸ tool_name args ✓ 1.2s`)
— a running phase names the latest action in the turn-status line, and the
turn closes with one expandable notice (`remote turn N — K tool actions`)
carrying the real lines; deployments that predate the field stay quiet
(honest absence, never fabricated). Remote tool approvals arrive on the
usual card. The status chip `remote:<state>[/phase] · lease <t>` turns
yellow under five minutes of lease. `/venue status` prints runtime, sources,
web policy and lease; `/venue stop [<id>]` (or `t` on an `F` row) tears the
runtime down and keeps local resume; `/venue sources` opens the picker in
manage mode; `/venue approvals` surfaces pending remote approvals.

Resume (`↵` or `/sessions resume <id>`) replays from the server. Remote-only
rows (server journal, no local row) can be resumed — the bridge pulls them
first — but rename, pin, archive and export need a local row and say so.
Filters `s` (all / ongoing / stopped / completed / error / archived), `v`
(all / local / frontend) and `A` (archives) are sent to the server, never
emulated. `x` exports a markdown transcript; `f` in the transcript cursor
forks the live session (`session.fork`). Durable chat-turn authority
(ADR-057) is still Proposed — that is the D-30 gap.

Details: [Frontend-venue sessions](../user-manual/30-frontend-venue.md),
remote-session TUI wave 2026-09-21,
[ADR-046](../adr/046-remote-session-credential-egress.md).

---

## 8. Knowledge in the console

- **Build strip** (top of the Knowledge pane): `build <project>: ✓ plan →
  ◐ ingest → · extract → · coverage → · graph · next: …`, derived only from
  client-visible artifacts (poll records, loaded graph, running ingest jobs);
  idle projects get the `/project ingest <id>` hint. A quota line rides
  under it when governance data is loaded.
- **Acquisition gate**: `/acquisition` reads the session's knowledge-write
  mode; `open` lets the agent propose or extend graphs (each write still
  gated by approval), `off` forbids knowledge writes this session, `extend
  <graph>[,<g2>…]` pins writes to named pre-existing graphs. The server
  enforces it (`set_knowledge_acquisition`); refused tool calls reach the
  model honestly.
- **Graph selection**: the server graph lane lists the project's registered
  graphs (click a row to load it); labels carry node/edge counts and a
  `truncated` marker when the content read was bounded. It falls back to the
  local plan projection, labelled as such, when the spine read is
  unavailable. Agent graph-extension proposals render beneath it with
  `[a]pprove` / `[r]eject`; `a` / `r` decide the selected proposal (`▸`,
  the first until `]` / `[` or a click moves it; until 2026-10-24 they
  always took the first). The header shows the selection's position
  (`2/7 agent proposals`) and the keys. A decision refreshes the inbox.
  While the citation preview is open these keys do nothing, even if a click
  gave the pane focus.
- **Consistency glyphs** (server-derived, never computed in the console):
  `✓` COMPLETE · `◐` EMBEDDING_PENDING (not an error) · `▲` PARTIAL ·
  `✗` ORPHANED · `·` no canonical document minted · `…` outside the
  reconcile sample ([ADR-031](../adr/031-knowledge-layer-plane-ownership.md)).
- **Cited replies**: assistant messages carry a `cited N` chip; `k` on that
  row in the transcript cursor opens the first citation in Knowledge, and
  `o` opens a preview of the cited passage itself (`citation.open`, fetched
  under the server's access check; `←`/`→` or `[`/`]` step through the
  sources, `↑`/`↓` and PgUp/PgDn scroll, Esc closes). The preview wraps to
  its own width. A row carries its passage handle, so it still opens after
  the session has dropped the label. A reply that cited none of what it
  retrieved shows `retrieved N`, not `cited N`. Since knowledge plan K1.4 (2026-10-24) the knowledge
  tools (`search_docs`, `rag_query`, `knowledge_query`, `open_passage`)
  record their passages in the session's evidence ledger, so a grounded
  answer lists its sources without the pre-retrieval router; an answer that
  cites none lists the passages its turn retrieved under "Retrieved (not
  cited)".

See [Knowledge management](../user-manual/27-knowledge-management.md) and
[Autonomous research](../user-manual/26-autonomous-research.md) for the
project → knowledge → loop flow behind these panes.

---

## 9. Theme, motion, clipboard

**Themes** — `/theme` opens a picker with live preview (Esc restores);
`/theme <name>` applies directly; `/theme list` prints names. Thirteen
palettes: `aurora` (default), `tokyo-night`, `nord`, `dracula`,
`gruvbox-dark`, `catppuccin-mocha`, `solarized-dark`, `solarized-light`,
`monokai`, `one-dark`, `rose-pine`, `kanagawa`, `high-contrast`; aliases such
as `tokyo`, `gruvbox`, `mocha`, `hc` resolve. The choice persists at
`~/.config/r4/theme.json` (`$R4_CONFIG_DIR`); `R4_TUI_THEME=<name>`
overrides it at launch.

**Reduced motion** — `R4_TUI_STILL=1` freezes decorative animation
(spinners pin their first frame, placeholders stop rotating); state-change
motion stays.

**Transcript copy (OSC 52).** The console enables mouse reporting, so a
plain click-drag is captured by the app: finishing a drag copies the selected
text immediately (a notice reports `copied N chars … (osc52|pbcopy)`), and
`Ctrl+Y` copies an active selection again. Mouse-free: `Ctrl+G` enters the
transcript cursor, `↑`/`↓` move, `c` copies the message via OSC 52 (on macOS
also `pbcopy`, so Terminal.app works), `s` keeps the message in project
knowledge (`/save`, K1.6: yours lands established, an answer proposed; the
focused row's footer shows `s save tN`; on an answer, `s` works on the
turn's final reply; refused while a turn runs; the first save of a console
run asks once, press `s` or repeat `/save` within 8 s, 2026-10-25), `Esc`
returns to the
composer. The Knowledge pane refreshes after a save, and says "knowledge
list unavailable: …" (keeping its rows) when the server list cannot be read.
Terminal notes from the package README: iTerm2 needs "Allow clipboard
access"; wezterm, kitty and alacritty work as-is; under tmux set `set -g
set-clipboard on`; hold Shift while dragging (Option on iTerm2) or press
`Ctrl+Alt+M` to use the terminal's own selection. If a hard kill leaves the
terminal in mouse or alt-screen mode, run `reset`.

---

## 10. Environment variables

| Variable | Effect |
|---|---|
| `R4_TUI_BACKEND` | `cli` (default when `r4` is found) or `offline` |
| `R4_TUI_DEMO` | `1` = demo visuals + fixtures (same as `--demo`) |
| `R4_TUI_PYTHON`, `R4_TUI_WORKSPACE` | Set by `r4 console`: interpreter with `r4` installed; workspace root for shell, mentions and code view |
| `R4_TUI_BIN`, `R4_TUI_HOME`, `R4_TUI_DIST_BASE_URL`, `R4_TUI_VERSION` | Explicit binary path (wins over a checkout's source too); install root (`<home>/bin/r4-tui`); release asset base; release tag pin |
| `R4_TUI_THEME`, `R4_TUI_STILL` | Theme at launch; reduced motion |
| `R4_CONFIG_HOME` | Root for `v3-keymap.json` and `prompt-history.jsonl` (default `~/.config/r4`) |
| `R4_CONFIG_DIR` | Root for `theme.json` and `projects/<id>/permissions.json` (default `~/.config/r4`) |
| `R4_PERMISSION_APPROVAL_TTL` | The `approval_ttl_s` agent setting (default 1800); 2026-10-30: the per-call approval cache it timed was removed |
| `R4_SANDBOX` | `on` / `off`: the local tool sandbox for sessions that have no choice of their own (2026-10-30, ADR-101; see §7). `r4 --sandbox on\|off` sets it per launch through `R4_LAUNCH_SANDBOX`, which the bridge takes out of the environment |

---

## 11. What is not in the console

Use the CLI ([reference](reference/cli.md), [full command tree](../user-manual/13-cli-reference.md)) for:

- one-shot, scriptable turns: `r4 -p "…"` (headless; `--yolo`, `--fs-root`, `--secure` apply there);
- installing or inspecting the console binary: `r4 tui install`, `r4 tui status`;
- session scripting: `r4 companion sessions`, `r4 companion export <id>`,
  `r4 session start|venue|stop`;
- registering sources and their keys: `r4 source add … --api-key-env`,
  `r4 source list --json --remote`, `r4 source push|unpush --json` (the
  console can only push or unpush what the CLI registered);
- the sandbox side of a remote session (`r4 venue worker`, never run by hand);
- the Rich REPL fallback (`--legacy-tui`) and the other ~65 CLI groups
  (endpoint flags, MCP registration, RAG upload, workflow bundles).

Not available anywhere yet: a cluster venue for companion sessions (the
picker row explains why), live token streaming from a remote worker (the
console polls partial text instead), default F-key pane jumps (bind your
own).

---

## 12. Bridge RPC contract (developers)

*Added 2026-10-18 (plan H-4).* The console talks to the Python companion
bridge (`r4/src/r4/companion/ui/bridge_server.py`) over JSON lines. Results of
the covered verbs have a typed contract, one source and two generated copies:

| Layer | File | Kept in sync by |
|---|---|---|
| Source: one stdlib dataclass per result, derived from the handler code | [`rpc_schema.py`](../../r4/src/r4/companion/ui/rpc_schema.py) | edit by hand |
| JSON Schema per verb (`<verb>.schema.json`) | [`contracts/bridge/v1/`](../../contracts/bridge/v1/) | [`tools/rpc_schema_export.py`](../../tools/rpc_schema_export.py) `--write` / `--check` |
| TS types (`RpcResultMap`, `RpcResult<"verb">`) | [`rpc-types.gen.ts`](../../packages/r4-tui-v3/src/bridge/rpc-types.gen.ts) | [`tools/rpc_types_gen.py`](../../tools/rpc_types_gen.py) `--write` / `--check` |

Covered (42 verbs): `get_state`, `send_message`, `confirm_plan`,
`dispatch_slash_command`, every `session.*` verb, `workflow.*` plus
`workflow_describe`, `assets.*`, `admin.*`, `provenance.*`, `sweep.*`,
`trust_list`, `trust_grant`. Other verbs still return untyped results; their
families get schemas in later H-4 rounds.

**Concurrency (plan H-8, 2026-10-18).** Every served verb has a class in
[`rpc_concurrency.py`](../../r4/src/r4/companion/ui/rpc_concurrency.py); a test
fails on a verb without one.
- **read**: answered from a pool of daemon threads, during a turn too, admitted
  in arrival order (a later mutation waits for it); the reply is a copy taken at
  read time. `project_status` and `project_findings` are reads but run
  serialized, because they redirect stdout.
- **mutate**: runs alone on the stdin thread in arrival order; no read sees it
  half-applied. During a turn it is parked and runs, in order, when the turn ends.
- **turn** (`send_message`, `confirm_plan`, `dispatch_slash_command`): waits for
  earlier reads, then streams; reads sent during it see it in progress.
- **control** (`cancel_turn`): answered at once, also as an RPC during a turn.
- Deferred verbs (`session.side_question`, `team.plan_run`, `team.plan_cancel`)
  keep their own threads.

Console pollers need no `turnInFlight` guard. Add a new verb's class in
`rpc_concurrency.py` together with its handler.

**A second client (2026-10-31, [ADR-103](../adr/103-vscode-console-parity.md)).**
The VS Code extension spawns the same bridge
(`vscode-extension/src/bridge/companion-bridge.ts`). The wire both clients
share — argv, protocol check, line routing, reply errors, out-of-band
approval / ask-user / cancel lines, `TURN_METHODS` — lives in
[`src/bridge/wire.ts`](../../packages/r4-tui-v3/src/bridge/wire.ts)
(`jsonl-client.ts` re-exports it). How a turn's `response_*` events become
reply text (round breaks, the `response_done` remainder, turn ids) lives in
[`src/lib/turn-reply.ts`](../../packages/r4-tui-v3/src/lib/turn-reply.ts),
which the chat adapter and the extension's agent host both use. *(2026-11-01:)*
so do the chat queue rail, `lib/queue-rail.ts` (`chat-queue-rail.ts` is its
console wrapper), and the `!cmd` formatting, `lib/shell-relay.ts`
(`local-shell.ts` keeps the process and re-exports it). Keep `wire.ts`,
`lib/terminal-safe.ts`, `lib/turn-reply.ts`, `lib/queue-rail.ts`,
`lib/shell-relay.ts`, `lib/approval-options.ts`, `lib/save-args.ts`,
`lib/sandbox.ts`, `lib/web-access.ts`, `lib/chip-state.ts`,
`lib/mention-query.ts`, `lib/mention-chips.ts`, `lib/paste-tracker.ts` (the last
three run in the extension's webview: `paste-chips.ts` and `file-mentions.ts`
re-export them) and
`bridge/rpc.ts` free of Solid, OpenTUI and Bun: the extension bundles them, and
`vscode-extension/test/shared-modules.test.mjs` fails otherwise. A new slash
verb or pane needs a row in `vscode-extension/parity/console-parity.json` in
the same PR (`node vscode-extension/scripts/parity-matrix.mjs --write`, then
set its status); the VS Code CI job checks it.

Rules:

- Console code reads a covered result with `rpc(bridge, "verb", params, timeoutMs)`
  from `src/bridge/rpc.ts`, never `bridge.request(...) as {…}`. `rpc` forwards
  its arguments unchanged, so fakes and spies see the same call.
- Field conventions: a key declared with `absent(...)` may be left out (TS `key?:`);
  `Optional[X]` is always sent and may be `null`; `Dict[str, Any]` is an UMRS
  payload relayed as is. Results accept extra keys, so adding a key is not a
  breaking change. Removing, renaming or retyping one is: it bumps
  `BRIDGE_PROTOCOL_VERSION` (still 1) and `RPC_SCHEMA_VERSION` together.
- Changing a handler's result: edit `rpc_schema.py`, then run
  `python tools/rpc_schema_export.py --write && python tools/rpc_types_gen.py --write`.
- Tests: `r4/tests/test_bridge_rpc_contract.py` runs each covered handler
  against a real session store with the network faked and validates the output
  against its schema; `tests/test_bridge_rpc_schema_drift.py` fails when the
  schemas or the TS file are stale. The PTY fixture bridge
  (`tests/pty-live/fixture-bridge/server.ts`) types its replies by the
  generated types, so `tsc` rejects a fixture that drifts from the wire.

**Errors (wire shape; session cases added 2026-10-22, cycle 3).** A failed
request answers `{"id": …, "ok": false, "error": {"type", "message", "hint"?, "data"?}}`
(`_error_response` in `bridge_server.py`; the console's `BridgeRequestError`
keeps `hint`, `type` and `data`). `message` and `hint` are prose for people and
may change; clients branch on `error.data.code` only. Two codes exist, both
from the session store (*2026-10-30:* plus the four `sandbox.*` codes of
`session.sandbox_set`, last row):

| `error.data.code` | `error.type` | Other `data` keys | Meaning |
|---|---|---|---|
| `session_conflict` | `SessionConflictError` | `session_id`; `copy_id` (`""` when this console had nothing unsaved); `stage` | Another console saved the session first; the compare-and-swap save wrote nothing over it. When this console had unsaved conversation, it was kept as a new session, `copy_id`. |
| `session_corrupt` | `SessionCorruptError` | `session_id`, `column` | The stored row's `column` does not decode; the row was left untouched (`/sessions export <id>` saves it raw). |
| `sandbox.unavailable`, `sandbox.self_test_failed`, `sandbox.refused`, `sandbox.remote` | `SandboxBridgeError` | `reason` | *2026-10-30 (ADR-101):* a refused `session.sandbox_set`. No usable backend; the self-test failed (this includes a write root at or above `$HOME` or a temp directory, which fails the self-test at `/sandbox on`); an unknown mode, or a profile default that could not be written; a frontend session. The session's setting is unchanged. A root refused when a tool spawns comes back as the tool result `Error: sandbox.refused: <reason>`, not as a bridge error. |

`stage` (`CONFLICT_STAGES` in `r4/src/r4/companion/session_models.py`) says
whether the turn ran:

- `before_turn`: `send_message` (or `confirm_plan`) was refused before the
  model was asked; nothing ran. The console returns the text to an empty
  composer and reloads the tab before a resend.
- `after_turn`: the end-of-turn save lost. The model and every tool call
  already ran, and the turn is in `copy_id`. Never send the message again
  (its tools would run twice). The console keeps the rows and continues the
  tab in the copy.
- `""`: any other save (a queue append, a toggle, a slash command). The change
  named by the request was not saved. A refused `session.queue_append` (or a
  refused remote-only append in the frontend venue) takes its row out and
  returns its text, to an empty composer or else in a notice, with a
  "queue append failed" card. It does so even when the queue already
  cleared without sending: a frontend turn's transcript replaced the rows,
  or a frontend interrupt (2026-10-22, round 4: skipping those lost the
  text silently). The one exception is a row this console already settled.
  The turn ahead of it ended and sent the row as its own turn, so returning
  the text would run its tools twice. Or ↑, Ctrl+Enter, a local interrupt or
  a clear already gave the text back or dropped it. In those cases nothing
  is returned.

  A rollback needs a definite answer (2026-10-22, round 5). An append that
  got no answer may still have been applied, and in the frontend venue the
  server then runs the text. That covers the console's 10 s timeout, a
  bridge that exited mid-request, and the bridge's own HTTP timeout or
  broken read (`error.type` `R4TimeoutError`, or `R4Error` "Request
  failed"). The console then leaves the row and the composer alone and
  posts a "queue append unconfirmed — check the transcript before
  resending" card that carries the text. A remote transcript keeps that
  card. The definite answers are `applied: false`, an error reply of any
  other type, and "bridge unavailable". Code: `mayHaveLanded` in
  `src/lib/chat-queue-rail.ts`.

A bridge that predates `stage` sends no key; the console reads that as
`before_turn`. Tests: `r4/tests/test_companion/test_session_store_cas.py`,
`test_two_bridges_cas.py` (beside it) and
`packages/r4-tui-v3/tests/session-typed-errors.test.ts`.

Related: [console README](../../packages/r4-tui-v3/README.md) ·
core-flows UI plan 2026-09-15 ·
v3 simplification wave 2026-09-18 (COMPLETE; archived 2026-10-19) ·
[quick start](quickstart.md).
