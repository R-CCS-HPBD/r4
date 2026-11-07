/**
 * Test preload (bunfig.toml [test].preload): every `bun test` run gets a
 * scratch config home, so nothing under test reads or writes the user's
 * real ~/.config/r4 (prompt history, statusline, keymap overrides, the tab
 * layout that the next real launch would auto-resume — TUI audit r2
 * #34/#46). A test that needs its own home still sets R4_CONFIG_HOME and
 * restores the previous value afterwards (never `delete`).
 *
 * R4_TEST_DEFAULT_CONFIG_HOME names this shared default: layout writes stay
 * off there (src/tabs/layout-persist.ts layoutWritesAllowed).
 */
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

if (!process.env.R4_CONFIG_HOME) {
  const home = mkdtempSync(join(tmpdir(), "r4-tui-test-home-"));
  process.env.R4_CONFIG_HOME = home;
  process.env.R4_TEST_DEFAULT_CONFIG_HOME = home;
  process.on("exit", () => {
    try {
      rmSync(home, { recursive: true, force: true });
    } catch {
      /* best effort */
    }
  });
}
