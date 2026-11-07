/**
 * Title screen (W2 launch frame, reworked 2026-10): the current R4 logos
 * (block letters + legacy wave art) with a centered chat input below.
 * The screen STAYS until the user submits the first message (or Esc
 * skips) — no auto-dismiss timer.
 *
 * Animation decorates the logos THEMSELVES (no unrelated art): a shine
 * sweep travels across the R4 block letters and a brightness ripple
 * travels down the legacy wave art (lib/logo-animation.ts).
 *
 * The input routes through actions.submitChatText — the same grammar as
 * the console chat input (`!` shell, `/` slash dispatch, else model turn).
 */

import { createEffect, createSignal, For, onCleanup } from "solid-js";
import { useTerminalDims } from "../lib/terminal-dims";
import { actions } from "../stores";
import { topClaimant } from "../keymap/key-claims";
import { appendPromptHistory } from "../lib/prompt-history";
import { palette } from "../themes";
import { splashLinesWithLegacy, R4_LOGO, SPLASH_TAGLINE } from "../lib/splash";
import {
  LOGO_FRAME_MS,
  rippleRowColor,
  shineColumnColors,
} from "../lib/logo-animation";
import { t, fg, bold, type PasteEvent } from "@opentui/core";
import { pastedText } from "./key-trap";
import pkg from "../../package.json" with { type: "json" };

const TITLE_HINT = "enter to begin · esc to skip · /help for commands";
const INPUT_PLACEHOLDER = "Ask anything… (enter to start)";
const LEGACY_WAVE_ROWS = 18;
/** Rows under the logo column: gap + 3-row input + gap + hint line. */
const INPUT_BLOCK_ROWS = 6;
/** Rows of the logo column without the legacy wave (blank + 18 rows). */
const LOGO_COLUMN_BASE = (total: number) => total - 1 - LEGACY_WAVE_ROWS;

/** What of the logo column fits `height` rows (stab-focus, 2026-10-21).
 * The old budget measured the column WITHOUT the wave and then showed it,
 * so at 20-37 rows the centered column overflowed: the input and hint were
 * drawn off-screen (you typed blind at 80x24), and at ~22 rows on narrow
 * widths OpenTUI's native renderer segfaulted. Drop the wave first, then
 * the hint block, then everything but the input. */
export function splashBudget(height: number, totalLines: number): { wave: boolean; rows: number } {
  const base = LOGO_COLUMN_BASE(totalLines);
  if (height >= totalLines + INPUT_BLOCK_ROWS) return { wave: true, rows: totalLines };
  if (height >= base + INPUT_BLOCK_ROWS) return { wave: false, rows: base };
  // Logo + blank + tagline (the hint rows go).
  const logoAndTagline = R4_LOGO.length + 2;
  if (height >= logoAndTagline + INPUT_BLOCK_ROWS) return { wave: false, rows: logoAndTagline };
  return { wave: false, rows: 0 };
}

type Chunk = ReturnType<typeof bold>;

/**
 * Compose a dynamic list of styled chunks into one StyledText — `t` is a
 * tagged-template function, so a synthetic TemplateStringsArray of empty
 * separators lets us spread runtime-built chunks.
 */
function composeChunks(chunks: Chunk[]) {
  const separators = Array(chunks.length + 1).fill("");
  const strings = Object.assign(separators, { raw: [...separators] }) as TemplateStringsArray;
  return t(strings, ...chunks);
}

export function SplashOverlay() {
  const dims = useTerminalDims();
  const [tick, setTick] = createSignal(0);
  const [draft, setDraft] = createSignal("");

  // Animation driver — unmount (splash close) stops it permanently via cleanup.
  const timer = setInterval(() => setTick((n) => n + 1), LOGO_FRAME_MS);
  onCleanup(() => clearInterval(timer));

  const lines = () => splashLinesWithLegacy(pkg.version);
  const legacyStart = () => LOGO_COLUMN_BASE(lines().length);
  /** Widest static content row — the column the screen centers on, never
   * wider than the terminal (an over-wide column overflowed both sides). */
  const columnWidth = () =>
    Math.min(Math.max(...lines().map((line) => line.length), TITLE_HINT.length, 44), Math.max(1, dims().width - 2));

  /** Height budget: drop the legacy wave first on short terminals. */
  const budget = () => splashBudget(dims().height, lines().length);
  const showLegacyWave = () => budget().wave;

  const inputWidth = () =>
    Math.min(Math.max(36, Math.min(72, dims().width - 8)), Math.max(1, dims().width - 2));

  /** Per-column shine colors for the R4 block letters at this tick. */
  const logoColors = () =>
    shineColumnColors(R4_LOGO[0].length, tick(), palette.accent, palette.fgBright);

  // Esc (or any other close) skips to chat WITH the typed text: it lands in
  // the composer draft instead of vanishing (stab-focus, 2026-10-21).
  let submitted = false;
  onCleanup(() => {
    if (!submitted && draft().trim()) actions.setPendingChatDraft(draft());
  });

  const submit = () => {
    const text = draft().trim();
    submitted = true;
    actions.closeSplash();
    if (text.length === 0) return;
    // A splash prompt is a prompt: it enters the history like the composer's.
    appendPromptHistory(text);
    void actions.submitChatText(text);
  };

  // An overlay opened over the title screen (F12 Help, Ctrl+P) takes the
  // keys; when it closes, remount the input so focus returns to it (the
  // OpenTUI input does not re-grab focus on a pre-existing node).
  const ownsKeys = () => topClaimant("letters")?.owner === "splash";
  const [inputEpoch, setInputEpoch] = createSignal(0);
  let owned = ownsKeys();
  createEffect(() => {
    const now = ownsKeys();
    if (now && !owned) setInputEpoch((e) => e + 1);
    owned = now;
  });

  // A paste with line breaks moves to the composer, which keeps them (chip /
  // multi-line draft): the single-line input here dropped every newline.
  const handlePaste = (event: PasteEvent) => {
    const text = pastedText(event);
    if (!text.includes("\n")) return; // single line: native paste
    event.preventDefault();
    const carried = `${draft()}${text}`;
    submitted = true; // the cleanup must not overwrite the carried draft
    actions.closeSplash();
    actions.setPendingChatDraft(carried);
  };

  const handleKey = (event: { name: string }) => {
    if (event.name === "escape") {
      actions.closeSplash();
    } else if (event.name === "return" || event.name === "enter") {
      submit();
    }
  };

  return (
    <box
      position="absolute"
      left={0}
      top={0}
      width="100%"
      height="100%"
      backgroundColor={palette.bg}
      flexDirection="column"
      alignItems="center"
      justifyContent="center"
      gap={1}
    >
      <box width={columnWidth()} flexDirection="column" alignItems="flex-start" overflow="hidden">
        {lines().map((line, idx) => {
          if (idx >= budget().rows) return null;
          const isLogo = idx < R4_LOGO.length;
          const isLegacy =
            showLegacyWave() && idx >= legacyStart() && line.trim().length > 0;
          if (idx >= legacyStart() && !showLegacyWave()) return null;
          if (isLogo) {
            // R4 block letters — per-character shine sweep (bold preserved).
            const colors = logoColors();
            const chunks = line.split("").map((ch, i) => bold(fg(colors[i])(ch)));
            return <text wrapMode="none">{composeChunks(chunks)}</text>;
          }
          // No wrap: a wrapped hint grows the column past the budget.
          return (
            <text
              wrapMode="none"
              fg={
                isLegacy
                  ? rippleRowColor(
                      idx - legacyStart(),
                      tick(),
                      palette.accent2,
                      palette.fgBright,
                    )
                  : line.includes(SPLASH_TAGLINE)
                    ? palette.fg
                    : palette.fgDim
              }
            >
              {line}
            </text>
          );
        })}
      </box>
      <box
        width={inputWidth()}
        height={3}
        backgroundColor={palette.msgUserBg}
        flexDirection="row"
        gap={1}
        paddingLeft={1}
        paddingRight={1}
      >
        <text fg={palette.accent}>›</text>
        <For each={[inputEpoch()]}>
          {() => (
            <input
              placeholder={INPUT_PLACEHOLDER}
              value={draft()}
              width="100%"
              focused={ownsKeys()}
              onInput={(value: string) => setDraft(value)}
              onKeyDown={handleKey}
              onPaste={handlePaste}
              backgroundColor={palette.msgUserBg}
              textColor={palette.fgBright}
              placeholderColor={palette.fg}
              cursorColor={palette.accent}
            />
          )}
        </For>
      </box>
      <box width={Math.min(TITLE_HINT.length, Math.max(1, dims().width - 2))} height={1} overflow="hidden">
        <text wrapMode="none" fg={palette.fgDim}>{TITLE_HINT}</text>
      </box>
    </box>
  );
}
