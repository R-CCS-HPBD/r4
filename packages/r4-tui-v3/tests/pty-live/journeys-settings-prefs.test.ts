/**
 * pty-live journey (issue #217, 2026-10-27): the Settings pane's Preferences
 * in the real dev TUI against the fixture bridge — the palette opens the
 * pane, Tab hands it the keys, Enter toggles the completion bell in place,
 * Tab cycles Preferences → Skills → MCP and back without leaving the pane,
 * and Enter on Theme opens the /theme picker.
 *
 * Opt-in like the rest of the tier:
 * `R4_PTY_LIVE=1 bun test tests/pty-live/journeys-settings-prefs.test.ts`.
 */

import { describe, it, expect } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const RUN = process.env.R4_PTY_LIVE === "1";
const suite = RUN ? describe : describe.skip;
const JOURNEY_MS = 90_000;
const CTRL_P = "\x10";
const TAB = "\t";

async function boot(): Promise<TuiProcess> {
  const tui = await spawnTui({ fixture: true, rows: 50, cols: 160 });
  const first = await tui.waitFor("esc to skip", 20_000);
  if (first.includes("esc to skip")) tui.send(KEYS.esc);
  await tui.waitFor("Chat with R4", 20_000);
  await tui.waitFor("workspace ready", 20_000);
  return tui;
}

async function expectScreen(tui: TuiProcess, text: string, timeoutMs = 10_000): Promise<string> {
  const frame = await tui.waitFor(text, timeoutMs);
  if (!frame.includes(text)) throw new Error(`"${text}" never rendered; last frame:\n${frame}`);
  return frame;
}

async function press(tui: TuiProcess, key: string): Promise<void> {
  tui.send(key);
  await tui.pump(250);
}

suite("pty journey: Settings Preferences", () => {
  it(
    "palette → Settings; Tab in; Enter toggles the bell; Tab cycles the panels; Theme opens its picker",
    async () => {
      const tui = await boot();
      try {
        await press(tui, CTRL_P);
        tui.send("Open pane: Settings");
        await expectScreen(tui, "Open pane: Settings", 5_000);
        await press(tui, KEYS.enter);
        await expectScreen(tui, "Completion bell: on (this session)");
        await press(tui, TAB); // chat → pane
        await press(tui, KEYS.enter);
        await expectScreen(tui, "Completion bell: off (this session)");
        // Preferences → Skills → MCP → Preferences: the keys stay in the pane.
        await press(tui, TAB);
        await press(tui, TAB);
        await press(tui, TAB);
        await press(tui, KEYS.enter);
        await expectScreen(tui, "Completion bell: on (this session)");
        await press(tui, KEYS.down);
        await press(tui, KEYS.down);
        expect(tui.screen.dump()).toContain("Enter opens the /theme picker");        await press(tui, KEYS.enter);
        await expectScreen(tui, "Choose a color theme");
        await press(tui, KEYS.esc);
      } finally {
        await tui.kill();
      }
    },
    JOURNEY_MS,
  );
});
