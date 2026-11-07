/**
 * The App removes its SIGINT/SIGTERM handlers on unmount: every mount added
 * two process listeners that kept that App reachable (2026-10-17).
 */
import { expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { actions, resetWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { resetTabsRegistryForTests } from "../src/tabs/registry";

it("mount and destroy leave no extra SIGINT/SIGTERM listeners", async () => {
  const saved = process.env.R4_TUI_BACKEND;
  process.env.R4_TUI_BACKEND = "offline";
  const counts = () => ["SIGINT", "SIGTERM", "SIGHUP"].map((s) => process.listenerCount(s as never));
  const before = counts();
  try {
    for (let i = 0; i < 3; i++) {
      const setup = await testRender(() => <App />, { width: 120, height: 40 });
      await setup.renderOnce();
      actions.closeAllOverlays();
      await new Promise((r) => setTimeout(r, 60)); // the async registration lands
      setup.renderer.destroy();
    }
    expect(counts()).toEqual(before);
  } finally {
    stopBackendPoller();
    stopKnowledgePoller();
    resetWorkspace();
    resetTabsRegistryForTests();
    if (saved === undefined) delete process.env.R4_TUI_BACKEND;
    else process.env.R4_TUI_BACKEND = saved;
  }
});
