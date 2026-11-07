/**
 * Shared harness for the stab-focus pins (lane `focus`, 2026-10-21): a
 * hermetic offline <App/>, settle, and a recording companion bridge.
 */
import { beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../../src/app";
import { actions, resetWorkspace } from "../../src/stores";
import { setDemoMode } from "../../src/lib/console-mode";
import { agentActions } from "../../src/stores/agent-runtime";
import { stopBackendPoller } from "../../src/hooks/useBackend";
import { stopKnowledgePoller } from "../../src/hooks/useKnowledge";
import { setCompanionBridgeForTests } from "../../src/bridge/jsonl-client";

export type Setup = Awaited<ReturnType<typeof testRender>>;

const savedBackend = process.env.R4_TUI_BACKEND;

function reset(): void {
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
}

/** Register the per-test reset hooks (call once at a test file's top). */
export function installStabHarness(): void {
  beforeEach(() => {
    process.env.R4_TUI_BACKEND = "offline";
    reset();
  });
  afterEach(async () => {
    await new Promise((r) => setTimeout(r, 50));
    setCompanionBridgeForTests(null);
    reset();
    if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
    else process.env.R4_TUI_BACKEND = savedBackend;
  });
}

export async function settle(setup: Setup, ms = 80): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

export async function mountApp(width = 160, height = 50): Promise<Setup> {
  const setup = await testRender(() => <App />, { width, height });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await settle(setup);
  return setup;
}

/** A recording companion bridge: every call lands in `calls`. */
export function recordingBridge(calls: string[]): void {
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    shutdown: async () => {},
    cancelTurn: () => calls.push("cancelTurn"),
    abortInFlightSend: () => calls.push("abortInFlightSend"),
    respondAskUser: (_id: unknown, answer: unknown) => calls.push(`respondAskUser(${String(answer)})`),
    respondApproval: (_id: unknown, decision: unknown) => calls.push(`respondApproval(${String(decision)})`),
    request: async (method: string) => {
      calls.push(`request:${method}`);
      return {};
    },
  } as never);
}
