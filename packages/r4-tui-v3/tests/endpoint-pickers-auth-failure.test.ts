/**
 * Signed out, /models, /endpoint up and /endpoint down name the gateway
 * failure (stabilization 2026-10-21, slash-a#0). They reported an empty
 * registry / endpoint list instead, and /endpoint up pointed back at
 * itself ("No registry models found — try /endpoint up <model>").
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { BridgeRequestError, setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";

const AUTH = () => new BridgeRequestError("Failed to list endpoints: Authentication failed", "run /login <JWT>");

let savedBackend: string | undefined;
beforeEach(() => {
  savedBackend = process.env.R4_TUI_BACKEND;
  delete process.env.R4_TUI_BACKEND;
  setDemoMode(false);
  resetWorkspace();
  setCompanionBridgeForTests({
    request: async (method: string) => {
      if (method === "list_models") return { models: [], current: "", problems: [] };
      throw AUTH();
    },
  } as never);
});
afterEach(() => {
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
  setCompanionBridgeForTests(null);
  resetWorkspace();
});

const lastNotice = () => workspace.chatMessages.filter((m) => m.role === "system").at(-1)?.content ?? "";

describe("slash-a#0 — signed-out pickers name the auth failure", () => {
  test("/endpoint up", async () => {
    await actions.dispatchSlashFromChat("/endpoint up");
    expect(lastNotice()).toBe(
      "/endpoint up: could not list registry models — Failed to list endpoints: Authentication failed → run /login <JWT>",
    );
  });

  test("/endpoint down", async () => {
    await actions.dispatchSlashFromChat("/endpoint down");
    expect(lastNotice()).toContain("/endpoint down: could not list endpoints — Failed to list endpoints: Authentication failed");
  });

  test("/models", async () => {
    await actions.dispatchSlashFromChat("/models");
    expect(lastNotice()).toBe("no models — gateway: Failed to list endpoints: Authentication failed → run /login <JWT>");
  });

  test("a later /endpoint up that lists fine does not repeat an old failure", async () => {
    await actions.dispatchSlashFromChat("/endpoint up");
    setCompanionBridgeForTests({ request: async () => ({ models: [] }) } as never);
    await actions.dispatchSlashFromChat("/endpoint up");
    expect(lastNotice()).toBe("No registry models found — try /endpoint up <model> or /health");
  });
});
