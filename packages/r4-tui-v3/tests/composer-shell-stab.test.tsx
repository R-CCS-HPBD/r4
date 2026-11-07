/**
 * `!cmd` local shell (stabilization 2026-10-21):
 *  - composer#11: Ctrl+C stops a running `!` command (it only armed the app
 *    exit; a hung `!tail -f` was stoppable only by quitting), and a stopped
 *    command is not relayed to the model.
 *  - static-sweep#4: the preview and the model relay belong to the tab that
 *    ran the command, never to the tab active when the shell finishes.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";

import { App } from "../src/app";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { getRegisteredHandlers } from "../src/keymap";
import { localShellRunning, runLocalShell } from "../src/lib/local-shell";
import { ctrlCAction } from "../src/lib/tui-parity";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { resetTabsRegistryForTests, runtimeOrNull } from "../src/tabs/registry";

const savedShell = process.env.SHELL;

beforeEach(() => {
  process.env.SHELL = "/bin/sh";
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(() => {
  resetWorkspace();
  if (savedShell === undefined) delete process.env.SHELL;
  else process.env.SHELL = savedShell;
});

const texts = (rows: ReadonlyArray<{ content: string }>) => rows.map((m) => m.content).join("\n");

describe("Ctrl+C stops a running `!` command (composer#11)", () => {
  it("decides stop-shell while a shell runs (after a turn / slash command, before the draft)", () => {
    expect(ctrlCAction({ streaming: false, draft: "typed", shellRunning: true })).toBe("stop-shell");
    expect(ctrlCAction({ streaming: true, draft: "", shellRunning: true })).toBe("interrupt");
    expect(ctrlCAction({ streaming: false, draft: "", slashRunning: true, shellRunning: true })).toBe("stop-slash");
  });

  it("the global Ctrl+C handler interrupts the command's whole process group", async () => {
    const setup = await testRender(() => <App forceOffline />, { width: 160, height: 48 });
    await setup.renderOnce();
    const started = Date.now();
    const run = runLocalShell("sleep 25; echo slept");
    await new Promise((resolve) => setTimeout(resolve, 150));
    expect(localShellRunning()).toBe(true);
    getRegisteredHandlers()["input.interrupt"]?.();
    const result = await run;
    setup.renderer.destroy();
    expect(Date.now() - started).toBeLessThan(5000);
    expect(result.interrupted).toBe(true);
    expect(result.stdout).not.toContain("slept");
    expect(localShellRunning()).toBe(false);
    expect(workspace.chatMessages.some((m) => m.content.includes("again to exit"))).toBe(false);
  });

  it("a stopped command is shown but not relayed to the model", () => {
    const tabId = workspace.activeChatTabId;
    actions.finishLocalShell(tabId, "sleep 25", {
      command: "sleep 25",
      exitCode: 130,
      stdout: "",
      stderr: "",
      durationMs: 300,
      interrupted: true,
    });
    const all = texts(workspace.chatMessages);
    expect(all).toContain("stopped with Ctrl+C");
    expect(all).not.toContain("I ran `!sleep 25`");
  });
});

describe("`!cmd` output lands on the tab that ran it (static-sweep#4)", () => {
  it("a tab switch while the shell runs keeps the result (and any relay) off the new tab", async () => {
    resetTabsRegistryForTests();
    const tabA = workspace.activeChatTabId;
    const pending = actions.submitChatText("!sleep 0.3; echo SHELL-OUT-FROM-A");
    await new Promise((resolve) => setTimeout(resolve, 50));
    const tabB = actions.tabCreate({ title: "B" });
    await pending;
    expect(workspace.activeChatTabId).toBe(tabB!);
    const onB = texts(workspace.chatMessages);
    expect(onB).not.toContain("SHELL-OUT-FROM-A");
    expect(onB).not.toContain("I ran `!");
    const onA = texts(runtimeOrNull(tabA)!.tabStore!.state.chatMessages);
    expect(onA).toContain("SHELL-OUT-FROM-A");
    expect(onA).toContain("not sent to the model");
  });
});
