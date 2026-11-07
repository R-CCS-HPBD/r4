/**
 * A signed-out /evidence or /circles names the console's sign-in remedy
 * (slash-b#8): the bridge's error carries the hint (AuthenticationError
 * tui_hint), and both commands printed the message alone.
 */
import { describe, it, expect, beforeEach, afterEach, mock } from "bun:test";
import { resetWorkspace, workspace } from "../src/stores";
import { BridgeRequestError } from "../src/bridge/jsonl-client";

const SIGN_IN = "not signed in, or the gateway rejected the token — /connect (or /login <token>) to sign in";
let failure: Error = new BridgeRequestError("Authentication failed", SIGN_IN, "AuthenticationError");

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    request: async () => {
      throw failure;
    },
  }),
}));

const { handleEvidenceCommand } = await import("../src/commands/evidence-command");
const { handleCirclesCommand } = await import("../src/commands/circles-command");

function lastNotice(): string {
  const systems = workspace.chatMessages.filter((m) => m.role === "system");
  return systems[systems.length - 1]?.content ?? "";
}

describe("signed-out commands name the sign-in remedy", () => {
  beforeEach(() => {
    resetWorkspace();
    failure = new BridgeRequestError("Authentication failed", SIGN_IN, "AuthenticationError");
  });
  afterEach(() => resetWorkspace());

  it("/evidence shows the hint on its own line", async () => {
    await handleEvidenceCommand("");
    expect(lastNotice()).toBe(`evidence failed: Authentication failed\n  → ${SIGN_IN}`);
  });

  it("/circles shows the hint on its own line", async () => {
    await handleCirclesCommand("");
    expect(lastNotice()).toBe(`circles: Authentication failed\n  → ${SIGN_IN}`);
  });

  it("an error without a hint stays one line", async () => {
    failure = new Error("boom");
    await handleCirclesCommand("");
    expect(lastNotice()).toBe("circles: boom");
  });
});
