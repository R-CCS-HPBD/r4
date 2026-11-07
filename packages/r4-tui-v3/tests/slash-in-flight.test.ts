/**
 * Ctrl+C stops a running companion slash command (2026-10-17). The bridge
 * runs dispatch_slash_command under the chat-turn pump and honours
 * cancel_turn; the keymap only interrupted while a chat turn streamed.
 */
import { describe, test, expect, beforeEach } from "bun:test";
import { ctrlCAction } from "../src/lib/tui-parity";
import { slashInFlight } from "../src/lib/slash-in-flight";
import { dispatchSlashCommand } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

describe("ctrlCAction with a running slash command", () => {
  test("a streaming turn still wins, then the slash command, then the draft", () => {
    expect(ctrlCAction({ streaming: true, draft: "", slashRunning: true })).toBe("interrupt");
    expect(ctrlCAction({ streaming: false, draft: "half", slashRunning: true })).toBe("stop-slash");
    expect(ctrlCAction({ streaming: false, draft: "half", slashRunning: false })).toBe("clear");
    expect(ctrlCAction({ streaming: false, draft: "" })).toBe("exit-chord");
  });
});

describe("slashInFlight", () => {
  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
  });

  test("is true while dispatch_slash_command is pending and false after", async () => {
    let release: (value: unknown) => void = () => {};
    const seen: boolean[] = [];
    setCompanionBridgeForTests({
      request: (method: string) => {
        if (method !== "dispatch_slash_command") return Promise.resolve({});
        seen.push(slashInFlight());
        return new Promise((resolve) => {
          release = resolve;
        });
      },
    } as never);
    try {
      const pending = dispatchSlashCommand("/knowledge MOF-5 capture");
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(slashInFlight()).toBe(true);
      release({ handled: true, output: [{ kind: "system", text: "done" }], state: {} });
      await pending;
      expect(seen).toEqual([true]);
      expect(slashInFlight()).toBe(false);
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});
