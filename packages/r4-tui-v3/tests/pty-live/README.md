# pty-live — keystroke-level E2E for the TUI

**Status:** opt-in tier (P0 of `docs/development/r4-tui-core-flows-ui-plan-2026-09-15.md`).

The headless `@opentui/solid` testRender tier cannot see whole classes of
production bugs (paint-order between remounted shell and floating overlays,
focus ownership after a KeyTrap is destroyed). This tier spawns the real dev
TUI (`bun --preload @opentui/solid/preload src/cli.tsx`) inside a real
pseudo-terminal and drives it with raw keystrokes; frames are reconstructed
from the cursor-addressed paint stream by `screen.ts` (a minimal VT100 grid).

## Run

```sh
cd packages/r4-tui-v3
R4_PTY_LIVE=1 bun test tests/pty-live/pty.test.ts
# fixture/offline variant for the theme+focus scenario:
R4_PTY_LIVE=1 R4_TUI_BACKEND=offline bun test tests/pty-live/pty.test.ts
# dump frames to tests/pty-live/golden/:
R4_PTY_LIVE=1 R4_PTY_GOLDEN=1 bun test tests/pty-live/pty.test.ts
```

Without `R4_PTY_LIVE` all scenarios skip — the default `bun test` run is
unaffected (no backend requirement added). CI wiring (updated 2026-10-18):
the `tui-pty-live` job in `.github/workflows/ci.yaml` runs the offline
scenarios on every push and PR, advisory (not in CI Success) until it has a
green streak; the live-backend leg stays gated on
`vars.R4_PTY_LIVE_BACKEND == '1'` (plan D4).

## Offline journeys (fixture bridge) — plan H-9, 2026-10-18

`journeys.test.ts` drives whole user journeys through the real app with no
backend, no `r4`, no model and no user config:

```sh
cd packages/r4-tui-v3
bun run test:pty      # = R4_PTY_LIVE=1 bun test tests/pty-live/journeys.test.ts
# CI (setup-bun puts bun on PATH, not in ~/.bun/bin):
BUN_BIN="$(command -v bun)" bun run test:pty
```

How it works — `spawnTui({ fixture: true })` (`fixture-bridge/fixture.ts`):

- The TUI runs in its normal **CLI mode** (`R4_TUI_BACKEND=cli`), not the
  `offline` demo adapter, so the chat rail, approval and plan cards, per-tab
  bridges, the remote watcher and the backend poller are the production code.
- `R4_TUI_PYTHON` and a PATH `r4` point at bash shims in a scratch dir.
  `python -m r4.companion.ui.bridge_server` execs `fixture-bridge/server.ts`
  (one per chat tab, as the real bridge); `r4 …` CLI calls get canned JSON
  (one endpoint, one job; other `--json` listings `[]`; anything else exits 1).
- `server.ts` speaks the bridge's JSONL wire (`{id, method, params}` →
  `{id, ok, result}`, `{event}`, `{approval_request}`, `{type, payload}`
  control lines) with result shapes copied from
  `r4/src/r4/companion/ui/bridge_server.py`. `fixture-bridge/turns.ts` picks a
  canned turn by the prompt's first words (`fx slow`, `fx approve`, `fx plan`,
  `fx think`, `fx remote`; anything else is a short reply that reports how
  many earlier user turns the fixture session holds).
- `HOME`, `R4_CONFIG_HOME` and the XDG roots live in the scratch dir, removed
  by `kill()`. `R4_PTY_FIXTURE_LOG=<file>` appends every line the TUI sends
  to the bridge (debugging aid).

Journeys: compose → rewind → clear, approval card with feedback, plan card,
tab switch during a turn, background tab finish, slash forward with args
(typed and pasted), Esc ladder, infrastructure Tab, remote watcher, thought
block + citations. Known UI defects are pinned with `it.failing` (they pass
while the defect is there and turn red once it is fixed: flip them to `it`).

Fixture rules: a fixture reply must be a shape the real bridge can send
(check `bridge_server.py`), and every journey asserts on rendered screen
text, waiting on conditions (`waitFor`/`expectScreen`/`expectGone`) — a bare
`pump` only after a lone ESC or to let a focus change settle.

## Rules for new scenarios

1. Every user-visible interaction change gets one scenario or one assertion
   in an existing scenario.
2. Snap frames (`snap("name")`) at every meaningful boundary; goldens are for
   debugging, assertions stay inline text checks (no snapshot diffs in CI).
3. Lone ESC bytes are held as sequence-starts by terminal parsers — after
   `KEYS.esc` always `pump` ≥150ms before asserting (documented 2026-09-15).
4. Scenarios must leave the user's session files unchanged: no persistence
   mutations beyond what a user keystroke would do.
