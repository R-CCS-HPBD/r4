/**
 * ADR-099 D-2 (maintainer decision M-4): the user chooses the agent's web
 * access when a session opens, local or remote.
 *
 * Before RS-1 a new local session never asked, a remote one preselected
 * `public` in the source picker (`w` cycled it), and the first turn of a
 * session went out with whatever the server defaulted to.
 */

import { describe, it, expect } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, activeTab, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { WebAccessOverlay, applyWebChoice, type WebChoiceDeps } from "../src/components/web-access-overlay";
import {
  NO_CAPS,
  domainWithin,
  needsWebChoice,
  parseDomains,
  validateWebChoice,
  webAccessLabel,
  webCapsFromLimits,
  webPromptDefaults,
  type WebAccessChoice,
} from "../src/lib/web-access";
import { mapBridgeStateToWorkspace } from "../src/bridge/map-state";
import { mapSandbox } from "../src/lib/sandbox";
import { resetTermWidthForTest, syncTermWidth } from "../src/lib/term-width";
import { installStabHarness, settle } from "./helpers/stab-focus-harness";

installStabHarness();

type Call = { method: string; params: Record<string, unknown> };

/** A fake companion bridge: canned answers by method, every call recorded. */
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
      const answer = answers[method];
      if (answer instanceof Error) throw answer;
      return typeof answer === "function" ? (answer as (p: unknown) => unknown)(params) : (answer ?? {});
    },
  } as never);
  return calls;
}

const UNCHOSEN: WebAccessChoice = { mode: "", domains: [], chosenAt: "", source: "" };
const CAPS_LIMITS = { web: { allow: ["none", "allowlist", "public"], domains: [".arxiv.org"], suggested_domains: [".arxiv.org"] } };
const UNCHOSEN_WIRE = { mode: "", domains: [], chosen_at: "", source: "" };

// ── pure rules ─────────────────────────────────────────────────────────────

describe("web-access rules", () => {
  it("a domain cap removes public; no caps allow every mode", () => {
    expect(webCapsFromLimits(CAPS_LIMITS).allow).toEqual(["none", "allowlist"]);
    expect(webCapsFromLimits(null)).toEqual(NO_CAPS);
    expect(webCapsFromLimits({ web: { allow: ["none"] } }).allow).toEqual(["none"]);
  });

  it("validates the answer against the caps", () => {
    const caps = webCapsFromLimits(CAPS_LIMITS);
    expect(validateWebChoice("public", "", caps).error).toContain("not allowed");
    expect(validateWebChoice("allowlist", "", caps).error).toContain("at least one domain");
    expect(validateWebChoice("allowlist", "pypi.org", caps).error).toContain("outside the allowed domains");
    expect(validateWebChoice("allowlist", "export.arxiv.org, .arxiv.org", caps).choice).toEqual({
      mode: "allowlist",
      domains: ["export.arxiv.org", ".arxiv.org"],
    });
    expect(validateWebChoice("none", "ignored", NO_CAPS).choice).toEqual({ mode: "none", domains: [] });
    expect(parseDomains("https://x.org").error).toContain("not a domain");
    expect(domainWithin("a.arxiv.org", [".arxiv.org"])).toBe(true);
    expect(domainWithin("notarxiv.org", [".arxiv.org"])).toBe(false);
  });

  it("preselects the current, then the remembered choice, then the suggestions", () => {
    const remembered: WebAccessChoice = { mode: "allowlist", domains: ["pypi.org"], chosenAt: "", source: "remembered" };
    expect(webPromptDefaults(null, remembered, NO_CAPS)).toEqual({ mode: "allowlist", domainsText: "pypi.org" });
    const caps = webCapsFromLimits({ web: { allow: ["none", "public"], suggested_domains: ["a.org"] } });
    expect(webPromptDefaults(null, remembered, caps)).toEqual({ mode: "none", domainsText: "pypi.org" });
    expect(webPromptDefaults(null, null, caps).domainsText).toBe("a.org");
  });

  it("asks only for a reported, unchosen, non-live session", () => {
    expect(needsWebChoice(UNCHOSEN, undefined)).toBe(true);
    expect(needsWebChoice(null, undefined)).toBe(false); // bridge before RS-1
    expect(needsWebChoice({ ...UNCHOSEN, mode: "none" }, undefined)).toBe(false);
    expect(needsWebChoice(UNCHOSEN, "running")).toBe(false);
    expect(webAccessLabel({ mode: "allowlist", domains: ["a.org", "b.org"] })).toBe("allowlist (a.org, b.org)");
  });

  it("the snapshot's web_access reaches the tab (absent key: unknown)", () => {
    const patch = mapBridgeStateToWorkspace({ session: { web_access: { mode: "none", domains: [], source: "env" } } }, undefined);
    expect(patch.webAccess?.mode).toBe("none");
    expect(mapBridgeStateToWorkspace({ session: {} }, undefined).webAccess).toBeUndefined();
  });
});

// ── a new session asks ─────────────────────────────────────────────────────

describe("a new session asks for the web access", () => {
  it("the local answer rides the initialize call that creates the session", async () => {
    const calls = fakeBridge({
      "session.web_access_get": { web_access: UNCHOSEN_WIRE, remembered: null, preset: false },
      initialize: { session: { web_access: { mode: "none", domains: [], source: "prompt" } }, messages: [] },
      list_sessions: { sessions: [] },
    });
    actions.openWebAccessPrompt({ purpose: "new-local", remote: false });
    const setup = await testRender(() => <WebAccessOverlay />, { width: 100, height: 30 });
    await settle(setup, 150);
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Off");
    expect(frame).toContain("Allowlist");
    expect(frame).toContain("Public");
    expect(frame).toContain("Shell, Python and MCP tools are not filtered locally");
    setup.mockInput.pressEnter();
    await settle(setup, 150);
    const init = calls.find((c) => c.method === "initialize");
    expect(init?.params).toEqual({ venue: "local", web_access: { mode: "none", domains: [] } });
    expect(workspace.webAccessPrompt).toBeNull();
    setup.renderer.destroy();
  });

  // ADR-101 D-6: with the session's sandbox on, the local note names the
  // sandbox proxy; off, it says how to turn it on — both whole on screen.
  it("the local note follows the tab's sandbox", async () => {
    fakeBridge({ "session.web_access_get": { web_access: UNCHOSEN_WIRE, remembered: null, preset: false } });
    syncTermWidth(100); // the note's cap follows the terminal the shell synced
    actions.setSandbox(mapSandbox({ mode: "on", backend: "bwrap", available: true }));
    actions.openWebAccessPrompt({ purpose: "slash", remote: false });
    const setup = await testRender(() => <WebAccessOverlay />, { width: 100, height: 30 });
    await settle(setup, 150);
    const onFrame = setup.captureCharFrame();
    expect(onFrame).toContain("Sandbox on: shell and Python go through its proxy");
    expect(onFrame).toContain("own web setting.");
    actions.setSandbox(mapSandbox({ mode: "off", backend: "bwrap", available: true }));
    await settle(setup, 50);
    const offFrame = setup.captureCharFrame();
    expect(offFrame).toContain("Shell, Python and MCP tools are not filtered locally");
    expect(offFrame).toContain("/sandbox to filter them");
    setup.renderer.destroy();
    actions.closeWebAccessPrompt();
    actions.setSandbox(null);
    resetTermWidthForTest();
  });

  it("the remote options follow the caps (a domain cap removes Public)", async () => {
    const calls = fakeBridge({
      "session.web_access_get": { web_access: UNCHOSEN_WIRE, remembered: null, preset: false },
      "session.venue_policy": { session_id: "s", project_id: "p", choice_required: true, limits: CAPS_LIMITS, current: null },
    });
    actions.openWebAccessPrompt({ purpose: "new-remote", remote: true });
    const setup = await testRender(() => <WebAccessOverlay />, { width: 100, height: 30 });
    await settle(setup, 150);
    const frame = setup.captureCharFrame();
    expect(calls.some((c) => c.method === "session.venue_policy")).toBe(true);
    expect(frame).toContain("Off");
    expect(frame).toContain("Allowlist");
    expect(frame).not.toContain("Public");
    expect(frame).toContain("The sandbox proxy filters");
    // The one-row remote note has no blank second row under it (tui-lane
    // review 2026-10-30): the key hint is the next line.
    const lines = frame.split("\n");
    const at = lines.findIndex((l) => l.includes("The sandbox proxy filters"));
    expect(lines[at + 1]).toContain("Esc cancel");
    // Allowlist, prefilled from the suggestions, confirmed → the source picker opens with it.
    setup.mockInput.pressArrow("down");
    await settle(setup);
    expect(setup.captureCharFrame()).toContain("domains: .arxiv.org");
    setup.mockInput.pressEnter();
    await settle(setup);
    expect(workspace.pendingRemoteWebAccess).toEqual({ mode: "allowlist", domains: [".arxiv.org"] });
    expect(workspace.remoteSourcePickerOpen).toBe(true);
    expect(workspace.remoteSourcePickerMode).toBe("start");
    setup.renderer.destroy();
  });

  it("/web: down to Allowlist, type a domain, Enter → session.web_access_set", async () => {
    const calls = fakeBridge({
      "session.web_access_get": { web_access: { mode: "none", domains: [], chosen_at: "", source: "prompt" }, remembered: null, preset: false },
      "session.web_access_set": (p: unknown) => ({ web_access: { ...(p as object), chosen_at: "", source: "prompt" } }),
    });
    actions.openWebAccessPrompt({ purpose: "slash", remote: false });
    const setup = await testRender(() => <WebAccessOverlay />, { width: 100, height: 30 });
    await settle(setup, 150);
    setup.mockInput.pressArrow("down");
    await settle(setup);
    await setup.mockInput.typeText("pypi.org");
    await settle(setup);
    setup.mockInput.pressEnter();
    await settle(setup, 150);
    const set = calls.find((c) => c.method === "session.web_access_set");
    expect(set?.params).toEqual({ mode: "allowlist", domains: ["pypi.org"], source: "prompt" });
    expect(activeTab().webAccess?.mode).toBe("allowlist");
    setup.renderer.destroy();
  });
});

// ── the first submit asks on a session without a choice ────────────────────

describe("first submit (the composer flow: web-access-flows.test.tsx)", () => {
  it("the answer sends the held message", async () => {
    const sent: string[] = [];
    const original = actions.sendChatMessage;
    (actions as { sendChatMessage: typeof original }).sendChatMessage = (text: string) => void sent.push(text);
    try {
      const deps: WebChoiceDeps = {
        newSession: async () => true,
        setSessionWebAccess: async () => true,
        fetchSessions: () => undefined,
      };
      await applyWebChoice({ purpose: "first-submit", remote: false, pendingText: "held" }, { mode: "none", domains: [] }, deps);
      expect(sent).toEqual(["held"]);
    } finally {
      (actions as { sendChatMessage: typeof original }).sendChatMessage = original;
    }
  });
});

// ── ADR-102: public access without a search server says how to add one ────

describe("session.web_access_set notice", () => {
  it("a notice in the answer lands as a system line; none adds nothing", async () => {
    const { setSessionWebAccess } = await import("../src/bridge/session-bridge");
    const notice = "web_search needs a search server: run `r4 search-server up`";
    const wire = { mode: "public", domains: [], chosen_at: "", source: "prompt" };
    const noticeLines = () => activeTab().chatMessages.filter((m) => m.role === "system" && m.content === notice);
    fakeBridge({ "session.web_access_set": { web_access: wire } });
    expect(await setSessionWebAccess({ mode: "public", domains: [] })).toBe(true);
    await new Promise((r) => setTimeout(r, 20));
    expect(noticeLines()).toHaveLength(0);
    fakeBridge({ "session.web_access_set": { web_access: wire, notice } });
    expect(await setSessionWebAccess({ mode: "public", domains: [] })).toBe(true);
    await new Promise((r) => setTimeout(r, 20));
    expect(noticeLines()).toHaveLength(1);
  });
});
