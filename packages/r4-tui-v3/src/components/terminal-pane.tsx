/**
 * Terminal pane — the interactive PTY shell docked in the detail pane.
 *
 * Sizing is MEASURED, never estimated: the box ref + a per-frame read give
 * the laid-out cols/rows, and the PTY follows (the percentage math and the
 * flexbox engine disagree on the pane's width — that mismatch wrapped
 * command output mid-line and misaligned the next prompt).
 *
 * Pointer rules (2026-10-03 revision): a CLICK inside this box focuses the
 * terminal; a click in the chat column releases it; no key moves focus.
 * The shell cursor draws as a solid block when focused, hollow otherwise,
 * so focus is discoverable on sight.
 */

import { createEffect, createMemo, createSignal, onCleanup, onMount, Show, For } from "solid-js";
import { CliRenderEvents } from "@opentui/core";
import { useRenderer } from "@opentui/solid";
import { workspace, setWorkspace } from "../stores";
import { palette } from "../themes";
import { termSession, visibleRows } from "../terminal/pty-session";
import { StyledTextView } from "../ui/StyledTextView";

export function TerminalPane() {
  const renderer = useRenderer();
  let boxEl: { width: number; height: number; x: number; y: number } | undefined;
  // Measured geometry: cols = box width (the box is the Panel's content
  // child, so chrome is already excluded), rows = height minus the
  // always-reserved footer row.
  const [geo, setGeo] = createSignal({ cols: 0, rows: 0 });

  onMount(() => {
    const read = () => {
      if (!boxEl) return;
      // A not-yet-laid-out box reports width 0 (and near-0 during remount
      // transitions). NEVER forward implausible sizes to the PTY — xterm
      // reflow after a transient tiny size destroys the buffer (verified
      // 2026-10-03: 40x10 → 6x2 → 40x39 keeps only one line). Wait for the
      // real layout instead.
      if (boxEl.width < 10 || boxEl.height < 4) return;
      const cols = boxEl.width;
      const rows = boxEl.height - 1; // footer row reserved
      const current = geo();
      if (current.cols !== cols || current.rows !== rows) setGeo({ cols, rows });
      // The same layout geometry drives the wrap-aware copy: a screen-space
      // selection maps onto buffer rows only through this rect. Keep it
      // fresh — position can change at constant size (tab/split moves).
      termSession.setPaneRect({ x: boxEl.x, y: boxEl.y, cols, rows });
    };
    // First measurement must not wait for FRAME — a quiet console (pane
    // reattach with nothing else painting) emits no frames and the grid
    // would stay zero-sized forever. Stagger retries past first layout.
    read();
    for (const delay of [0, 40, 120, 300]) {
      setTimeout(read, delay);
    }
    renderer.on(CliRenderEvents.FRAME, read);
    onCleanup(() => renderer.off(CliRenderEvents.FRAME, read));
    // A closed pane holds no selection mapping — never copy through a stale
    // rect onto a detached buffer.
    onCleanup(() => termSession.setPaneRect(null));
  });

  // Size follows the measured box; the shell spawns on first mount and
  // reattaches on every remount (the session outlives the pane). First
  // layout may report zeros — skip spawning until the box is real.
  createEffect(() => {
    const { cols, rows } = geo();
    if (cols > 0 && rows > 0) termSession.ensure(cols, rows);
  });

  const rows = createMemo(() => {
    termSession.revision(); // reactive dep on every buffer change
    const { cols, rows: n } = geo();
    if (cols <= 0 || n <= 0) return [];
    const rendered = visibleRows({
      cols,
      rows: n,
      defaultFg: palette.fg,
      blockFg: palette.bgPanel,
      hollowBg: palette.bgHover,
      focused: workspace.paneFocus,
    });
    return rendered ?? [];
  });

  const status = createMemo(() => {
    termSession.revision();
    return termSession.status();
  });

  const footer = createMemo((): string | null => {
    const s = status();
    if (s.state === "unsupported") return s.reason ?? "terminal unsupported on this host";
    if (s.state === "exited") return `shell exited (code ${s.exitCode ?? "?"}) — r restarts · Esc closes the pane`;
    if (termSession.alternateActive) return workspace.paneFocus ? null : "shell running — click to focus";
    if (termSession.scrollOffset > 0) return `scrolled ${termSession.scrollOffset} — Shift+PgDn returns to the live tail`;
    return workspace.paneFocus ? null : "shell running — click to focus · drag the left bar to resize";
  });

  return (
    <box
      flexGrow={1}
      flexDirection="column"
      overflow="hidden"
      ref={(el) => (boxEl = el)}
      onMouseDown={() => {
        // Click-to-focus (the terminal's ONLY focus switch). The click
        // itself still counts: a drag selection starting here behaves like
        // anywhere else; we never consume the event.
        if (!workspace.paneFocus) setWorkspace("paneFocus", true);
      }}
    >
      <box flexGrow={1} flexDirection="column" overflow="hidden">
        <For each={rows()}>{(row) => <StyledTextView text={row} />}</For>
      </box>
      {/* Always reserved: the PTY row count must not flicker when the hint
          line appears/disappears. */}
      <box height={1} flexShrink={0} overflow="hidden">
        <Show when={footer()}>
          <text fg={palette.fgDim}>{footer()}</text>
        </Show>
      </box>
    </box>
  );
}
