# @r4/tui-v3 — R4 Research Operations Console

A terminal UI (TUI) console for the R4 platform: research dashboard, streaming
chat, infrastructure (endpoints/models/jobs) monitoring, and a live activity
console. Built with [Bun](https://bun.sh), [SolidJS](https://www.solidjs.com/),
and [OpenTUI](https://github.com/opentui/opentui).

## Requirements

- Bun `>= 1.3.14`
- Node `>= 22` (for tooling/type resolution)
- Optional: the `r4` CLI on `PATH` for live backend data (otherwise runs offline)

## Install

```bash
bun install
```

From the repo root, `make install` (or `make tui-deps`) runs `bun install --frozen-lockfile`
here and stamps it; `r4 console` from this checkout then runs this source, not a release
binary (2026-09-25; see `docs/r4/TUI_REFERENCE.md` §1).

## Run

```bash
# Start the console (dev-style)
bun run start

# Development entry (index)
bun run dev

# Explore offline with demo visuals and fixture data
bun run src/cli.tsx --demo

# Print version / help
bun run src/cli.tsx --version
bun run src/cli.tsx --help
```

## Controls

- `Ctrl+.` (or type `/` in chat) opens the slash-command bar. Type a command, use `Tab` to insert it
  (`Ctrl+/` is often swallowed by WSL/Windows terminals).
  into chat, or press `Enter` to run it.
- `Ctrl+P` opens the command palette for navigation and backend actions.
- `/help` lists slash commands; `/clear` clears chat; `/quit` exits.

### Transcript copy (mouse-free)

The console enables mouse reporting, so plain click-drag selection is captured
by the app and the terminal's native selection does not work. Copy transcript
text without the mouse:

1. `g,y` (`g` then `y` — but not inside chat text fields; `Shift+Tab` or
   `Ctrl+↑` also work) enters the transcript cursor on the latest message.
   (`ctrl+shift+c` was considered and dropped: most terminals deliver it as
   plain `ctrl+c` unless a kitty-keyboard-capable terminal is detected.)
2. `↑`/`↓` (or `j`/`k`) moves the cursor.
3. `c` copies the message via **OSC 52** — on macOS it also falls back to
   `pbcopy`, so Terminal.app works despite lacking OSC 52 support.
4. `Esc` exits the cursor (returns to chat input).

Terminal requirements for OSC 52: iTerm2 needs "Allow clipboard access" enabled;
wezterm/kitty/alacritty work out of the box; Terminal.app uses the pbcopy path;
under tmux set `set -g set-clipboard on`.

Native-selection fallback: hold **Shift** while dragging (iTerm2: **Option**+drag)
to bypass mouse reporting and use the terminal's own selection.

Terminal-state restore: OpenTUI owns mouse-mode and alt-screen setup/teardown on
exit. If a hard kill ever leaves the terminal in mouse-reporting or alt-screen
mode, run `reset` (or `printf '\e[?1000l\e[?1002l\e[?1003l\e[?1006l\e[?1049l'`).

### Backend selection

The console picks a data source automatically by probing for the `r4` CLI. You
can force one explicitly:

```bash
R4_TUI_BACKEND=offline bun run start   # offline demo visuals and fixtures
R4_TUI_BACKEND=cli     bun run start   # use the r4 CLI (chat, endpoints, jobs)
```

- **cli** — chat streams through `r4.client.Client.chat_stream` (invoked via a
  Python subprocess emitting JSONL deltas). Endpoint/job/model data is polled
  from the `r4` CLI with exponential backoff on failure and reconnect notices.
- **offline** — no backend; chat uses a local canned responder and backend
  actions return `(offline) would …` results.

## Features

- **Streaming chat** with per-message model attribution, offline-fallback
  flagging, `Esc Esc` to interrupt an in-flight stream (one stray `Esc` only
  arms; `R4_ESC_INTERRUPT_TAPS=1|2|3` sets the taps), and `↑`/`↓` command history.
- **Live infrastructure view** — endpoints, models, jobs, and a real load
  sparkline derived from in-flight jobs and busy endpoints.
- **Backend actions** from the command palette — provision/stop endpoints and
  cancel/retry jobs, with success/error notifications.
- **Resilient polling** — 8s poll with exponential backoff (capped) and
  reconnect/offline notifications.
- **Keys stay with r4** (2026-10-15, onboarding plan S4/G8) — `/source add …
  --api-key <k>` and `/login <token>` hand the key to `r4` on stdin, never on
  a command line. A key typed on a slash line or a `!` shell line (`--api-key`,
  `--token`, r4's `login -k`, `Bearer …`) is masked in the transcript, the
  task list and the model relay, and such a line is never kept in `↑` history
  or `prompt-history.jsonl`. The console sends no source key itself: `/source`
  and `/health` probe only keyless routes (a gateway's `/ready`, a keyless
  Ollama's `/api/tags` or OpenAI-style `/models`) and show a keyed source as
  `?` unknown; the `/models` picker fetches no catalog (visibility chips will
  come from `r4 source list --health --json`).

## Scripts

| Script            | Description                                    |
| ----------------- | ---------------------------------------------- |
| `bun run start`   | Launch the console                             |
| `bun run dev`     | Launch via the index entry                     |
| `bun run smoke`   | Initialize and exit (CI self-test)             |
| `bun run test`    | Run the test suite                             |
| `bun run typecheck` | Type-check with `tsc --noEmit`               |
| `bun run check`   | typecheck + test + smoke (full local gate)     |

## Testing

```bash
bun run check
```

Runs type checking, the full test suite, and a headless smoke test.
