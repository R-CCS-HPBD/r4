/**
 * Claude-Code-parity input/run-control logic (docs/development/
 * r4-tui-claude-parity-2026-09-24.md). Pure functions only — the chat panel
 * and the global keymap wire them; tests pin the decisions.
 */

/** Ctrl+C: interrupt a running turn, else stop a running companion slash
 * command, else stop a running `!` shell command, else clear the draft,
 * else the exit chord. */
export type CtrlCAction = "interrupt" | "stop-slash" | "stop-shell" | "clear" | "exit-chord";

export function ctrlCAction(state: {
  streaming: boolean;
  draft: string;
  slashRunning?: boolean;
  shellRunning?: boolean;
}): CtrlCAction {
  if (state.streaming) return "interrupt";
  if (state.slashRunning) return "stop-slash";
  if (state.shellRunning) return "stop-shell";
  if (state.draft.trim().length > 0) return "clear";
  return "exit-chord";
}

/** Double-press exit chord (Ctrl+C / Ctrl+D): first press arms, second within
 * the window exits. Returns the next armed timestamp (null after exit). */
export function exitChord(
  armedAt: number | null,
  now: number,
  windowMs: number,
): { exit: boolean; armedAt: number | null } {
  if (armedAt !== null && now - armedAt <= windowMs) return { exit: true, armedAt: null };
  return { exit: false, armedAt: now };
}

export const CTRL_C_WINDOW_MS = 2000;
export const CTRL_D_WINDOW_MS = 800;
export const DOUBLE_ESC_WINDOW_MS = 600;

/** Esc Esc: a second Esc within the window. */
export function isDoubleEsc(lastEscAt: number | null, now: number): boolean {
  return lastEscAt !== null && now - lastEscAt <= DOUBLE_ESC_WINDOW_MS;
}

/** Ctrl+R reverse-i-search: newest match strictly older than `before`
 * (case-insensitive substring). `before` = history.length starts at newest. */
export function reverseSearch(
  history: readonly string[],
  query: string,
  before: number,
): { index: number; text: string } | null {
  const needle = query.toLowerCase();
  for (let i = Math.min(before, history.length) - 1; i >= 0; i -= 1) {
    const text = history[i] ?? "";
    if (!needle || text.toLowerCase().includes(needle)) return { index: i, text };
  }
  return null;
}

/** Ctrl+S: stash a non-empty draft; on an empty draft restore the newest
 * stash. Stashes STACK: a second Ctrl+S used to overwrite the first stashed
 * draft silently (composer#6). */
export function stashToggle(
  draft: string,
  stash: readonly string[],
): { draft: string; stash: string[]; notice: string } {
  if (draft.trim().length > 0) {
    const next = [...stash, draft];
    const held = next.length > 1 ? ` (${next.length} stashed)` : "";
    return { draft: "", stash: next, notice: `draft stashed${held} — Ctrl+S on an empty prompt restores it` };
  }
  const restored = stash.at(-1);
  if (restored === undefined) return { draft, stash: [...stash], notice: "nothing stashed" };
  const rest = stash.slice(0, -1);
  const more = rest.length > 0 ? ` (${rest.length} more stashed)` : "";
  return { draft: restored, stash: rest, notice: `stashed draft restored${more}` };
}

/** A `!cmd` result relayed to the model as a user turn (local-shell.ts
 * formatShellForAgent) — machine content, not a prompt the user wrote. */
const SHELL_RELAY = /^I ran `!.*` in the TUI's local shell — /;

/**
 * A user row that is a chat prompt the user wrote: not a slash-command
 * echo (recordUserSlashCommand writes those as user rows), not a relayed
 * shell result, not still queued. /rewind and /undo act on these only —
 * listing echoes made the current /rewind itself the highlighted target
 * (stabilization 2026-10-21, slash-a#3 / composer#10).
 */
export function isChatPrompt(msg: { role: string; content: string; queued?: boolean }): boolean {
  if (msg.role !== "user" || msg.queued) return false;
  return !msg.content.startsWith("/") && !SHELL_RELAY.test(msg.content);
}

/** /rewind + Esc Esc menu: earlier chat prompts, newest first. */
export function rewindCandidates(
  messages: ReadonlyArray<{ role: string; content: string; queued?: boolean }>,
  previewChars = 72,
): Array<{ index: number; preview: string }> {
  const out: Array<{ index: number; preview: string }> = [];
  messages.forEach((msg, index) => {
    if (!isChatPrompt(msg)) return;
    const flat = msg.content.replace(/\s+/g, " ").trim();
    const preview = flat.length > previewChars ? `${flat.slice(0, previewChars - 1)}…` : flat;
    out.push({ index, preview });
  });
  return out.reverse();
}

/** Queue take-back / send-now: the queued prompts joined as one draft/turn. */
export function joinQueued(contents: readonly string[]): string {
  return contents.map((c) => c.trim()).filter(Boolean).join("\n\n");
}
