/**
 * ADR-099 D-2 (maintainer decision M-4): the console asks for the agent's
 * web access when a session opens — the flows through the pre-existing
 * surfaces (venue picker, composer, remote source picker).
 *
 * This file imports only modules that existed before RS-1, so on the old
 * code its assertions fail (rather than its imports): a new local session
 * was created at once, a remote one preselected `public` (`w` cycled it),
 * and the first turn went out with no question.
 */

import { describe, it, expect } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, activeTab, setScopedField, setWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { VenuePickerOverlay } from "../src/components/venue-picker-overlay";
import { RemoteSourcePickerOverlay } from "../src/components/remote-source-picker-overlay";
import { installStabHarness, mountApp, settle } from "./helpers/stab-focus-harness";

installStabHarness();

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge(answers: Record<string, unknown>, calls: Call[] = []): Call[] {
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    shutdown: async () => {},
    cancelTurn: () => {},
    abortInFlightSend: () => {},
    respondAskUser: () => {},
    respondApproval: () => {},
    request: async (method: string, params?: Record<string, unknown>) => {
      calls.push({ method, params: params ?? {} });
      return answers[method] ?? {};
    },
  } as never);
  return calls;
}

/** The tab's web-access choice, written as the snapshot mapping writes it. */
function tabWeb(mode: string, domains: string[] = []): void {
  setScopedField("webAccess" as never, { mode, domains, chosenAt: "", source: mode ? "prompt" : "" } as never);
}

/** The webAccessPrompt field (undefined on the old store). */
function prompt(): { purpose?: string; remote?: boolean; pendingText?: string } | null | undefined {
  return (workspace as unknown as Record<string, never>).webAccessPrompt;
}

describe("a new session asks for the web access", () => {
  for (const [key, purpose, remote] of [
    ["l", "new-local", false],
    ["f", "new-remote", true],
  ] as const) {
    it(`venue picker [${key}] → the web question (${purpose}), nothing created yet`, async () => {
      const calls = fakeBridge({ list_sessions: { remote_available: true, remote_reason: "" } });
      actions.openVenuePicker();
      const setup = await testRender(() => <VenuePickerOverlay />, { width: 100, height: 30 });
      await settle(setup, 150);
      setup.mockInput.pressKey(key);
      await settle(setup, 150);
      expect(workspace.venuePickerOpen).toBe(false);
      expect(prompt()?.purpose).toBe(purpose);
      expect(prompt()?.remote).toBe(remote);
      expect(workspace.remoteSourcePickerOpen).toBe(false); // the question comes first
      expect(calls.some((c) => c.method === "initialize")).toBe(false);
      setup.renderer.destroy();
    });
  }
});

describe("first submit", () => {
  it("a session without a choice asks before the turn; the message waits", async () => {
    const setup = await mountApp();
    tabWeb("");
    await setup.mockInput.typeText("hello web");
    await settle(setup);
    setup.mockInput.pressEnter();
    await settle(setup, 120);
    expect(prompt()?.purpose).toBe("first-submit");
    expect(prompt()?.pendingText).toBe("hello web");
    expect(activeTab().chatMessages.some((m) => m.role === "user" && m.content === "hello web")).toBe(false);
    // Esc (the sweep): the message goes back into the composer.
    actions.closeAllOverlays();
    await settle(setup, 120);
    expect(workspace.pendingChatDraft === "hello web" || activeTab().chatDraft === "hello web").toBe(true);
    setup.renderer.destroy();
  });

  it("a session with a choice sends at once", async () => {
    const setup = await mountApp();
    tabWeb("none");
    await setup.mockInput.typeText("hi there");
    await settle(setup);
    setup.mockInput.pressEnter();
    await settle(setup, 120);
    expect(prompt() ?? null).toBeNull();
    expect(activeTab().chatMessages.some((m) => m.role === "user" && m.content === "hi there")).toBe(true);
    setup.renderer.destroy();
  });
});

describe("remote source picker", () => {
  const ROW = { name: "openai-main", kind: "public", url: "https://api.example", has_api_key: true, remote: "pushed", eligible: true };

  it("w no longer cycles; the start sends the chosen web access", async () => {
    const calls = fakeBridge({
      "source.list_remote": { rows: [ROW], remote_available: true },
      initialize: { session: {}, messages: [] },
      "session.venue_start": { venue: { state: "provisioning" }, runtime: {}, state: null, adopted: false },
      list_sessions: { sessions: [] },
    });
    setWorkspace("pendingRemoteWebAccess" as never, { mode: "allowlist", domains: [".arxiv.org"] } as never);
    actions.openRemoteSourcePicker("start");
    const setup = await testRender(() => <RemoteSourcePickerOverlay />, { width: 140, height: 30 });
    await settle(setup, 150);
    expect(setup.captureCharFrame()).toContain("web: allowlist (.arxiv.org)");
    setup.mockInput.pressKey("w");
    await settle(setup);
    const frame = setup.captureCharFrame();
    expect(frame).toContain("web: allowlist (.arxiv.org)");
    expect(frame).not.toContain("w web:");
    setup.mockInput.pressEnter();
    await settle(setup, 200);
    expect(calls.find((c) => c.method === "initialize")?.params).toEqual({
      venue: "frontend",
      web_access: { mode: "allowlist", domains: [".arxiv.org"] },
    });
    const start = calls.find((c) => c.method === "session.venue_start");
    expect(start?.params.web_policy).toBe("allowlist");
    expect(start?.params.web_allow_domains).toEqual([".arxiv.org"]);
    setup.renderer.destroy();
  });

  it("/venue start on a session without a choice asks first", async () => {
    const calls = fakeBridge({ "source.list_remote": { rows: [ROW], remote_available: true } });
    tabWeb("");
    actions.openRemoteSourcePicker("current");
    const setup = await testRender(() => <RemoteSourcePickerOverlay />, { width: 140, height: 30 });
    await settle(setup, 150);
    expect(setup.captureCharFrame()).toContain("web: not chosen");
    setup.mockInput.pressEnter();
    await settle(setup);
    expect(workspace.remoteSourcePickerOpen).toBe(false);
    expect(prompt()).toEqual({ purpose: "venue-current", remote: true });
    expect(calls.some((c) => c.method === "session.venue_start")).toBe(false);
    setup.renderer.destroy();
  });

  it("/venue start on a default_off session (an r4 -p run) asks first (RS-1 round-3 N2)", async () => {
    const calls = fakeBridge({ "source.list_remote": { rows: [ROW], remote_available: true } });
    setScopedField("webAccess" as never, { mode: "none", domains: [], chosenAt: "", source: "default_off" } as never);
    actions.openRemoteSourcePicker("current");
    const setup = await testRender(() => <RemoteSourcePickerOverlay />, { width: 140, height: 30 });
    await settle(setup, 150);
    expect(setup.captureCharFrame()).toContain("web: not chosen");
    setup.mockInput.pressEnter();
    await settle(setup);
    expect(prompt()).toEqual({ purpose: "venue-current", remote: true });
    expect(calls.some((c) => c.method === "session.venue_start")).toBe(false);
    setup.renderer.destroy();
  });
});
