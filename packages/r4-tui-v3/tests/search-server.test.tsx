/**
 * ADR-102 guided start (2026-11-01): `/search-server [status|up|down]` over
 * search_server.status / .up / .down, the pushed `search_server_event` lines
 * as notices, and the offer after /web Public when no search server exists
 * (local session, podman or docker installed): Enter starts, Esc declines.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions, resetWorkspace, setScopedField, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { routeBridgeLine } from "../src/bridge/wire";
import { setSessionWebAccess } from "../src/bridge/session-bridge";
import { dispatchSlashCommand } from "../src/commands/slash";
import {
  handleSearchServerEvent,
  searchServerEventText,
  searchServerStatusText,
} from "../src/commands/search-server-command";
import { SearchServerOfferOverlay, acceptSearchServerOffer } from "../src/components/search-server-offer-overlay";
import type { RpcResult } from "../src/bridge/rpc";
import { searchServerBackend } from "../src/lib/search-server-state";
import { installStabHarness, settle } from "./helpers/stab-focus-harness";

installStabHarness();

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge(answers: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
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
      return answer ?? {};
    },
  } as never);
  return calls;
}

function status(over: Partial<RpcResult<"search_server.status">> = {}): RpcResult<"search_server.status"> {
  return { state: "absent", url: "", engine: "podman", starting: false, external_url: "", ...over };
}

const notices = () => workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);
const tick = () => new Promise((r) => setTimeout(r, 20));
const PUBLIC_WIRE = { mode: "public", domains: [], chosen_at: "", source: "prompt" };

beforeEach(() => {
  // The stab harness boots offline (it restores the variable after each
  // test); these tests talk to a fake live bridge.
  delete process.env.R4_TUI_BACKEND;
  resetWorkspace();
  setScopedField("sessionVenue", "local");
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  resetWorkspace();
});

describe("/search-server", () => {
  it("bare and status show the state; an absent server names how to start it", async () => {
    const calls = fakeBridge({ "search_server.status": status() });
    await dispatchSlashCommand("/search-server");
    expect(calls.map((c) => c.method)).toEqual(["search_server.status"]);
    expect(notices().at(-1)).toContain("/search-server up starts a local SearXNG with podman");
  });

  it("status texts: running, recorded but down, no engine, starting", () => {
    expect(searchServerStatusText(status({ state: "running", url: "http://127.0.0.1:8888" }))).toContain(
      "running at http://127.0.0.1:8888",
    );
    expect(searchServerStatusText(status({ state: "down", url: "u" }))).toContain("not answering");
    expect(searchServerStatusText(status({ engine: "" }))).toContain("no podman or docker found");
    expect(searchServerStatusText(status({ starting: true }))).toBe("search server: starting…");
  });

  it("R4_WEB_SEARCH_URL is named: the status no longer says none while web_search works (2026-11-03)", () => {
    const text = searchServerStatusText(status({ external_url: "https://searx.example.org" }));
    expect(text).toContain("R4_WEB_SEARCH_URL names https://searx.example.org");
    expect(text).not.toContain("none");
  });

  it("the web chip follows /search-server: status, up (starting), then the start's end", async () => {
    fakeBridge({ "search_server.status": status({ state: "running", url: "http://127.0.0.1:8888" }) });
    await dispatchSlashCommand("/search-server status");
    expect(searchServerBackend()).toBe("up");
    fakeBridge({ "search_server.up": { accepted: true, state: "starting" }, "search_server.status": status() });
    await dispatchSlashCommand("/search-server up");
    expect(searchServerBackend()).toBe("starting");
    handleSearchServerEvent({ phase: "error", state: "failed", reason: "no engine" });
    await tick();
    expect(searchServerBackend()).toBe("none");
  });

  it("up is accepted, then each pushed step and the end land as notices", async () => {
    const calls = fakeBridge({ "search_server.up": { accepted: true, state: "starting" } });
    await dispatchSlashCommand("/search-server up");
    expect(calls.map((c) => c.method)).toEqual(["search_server.up"]);
    expect(notices().at(-1)).toContain("starting a local SearXNG");
    handleSearchServerEvent({ phase: "progress", message: "downloading img (first start only, about 200 MB)" });
    handleSearchServerEvent({ phase: "done", state: "started", url: "http://127.0.0.1:8888", reason: "", hint: "" });
    expect(notices().slice(-2)).toEqual([
      "search server: downloading img (first start only, about 200 MB)",
      "✓ search server ready at http://127.0.0.1:8888 — web_search works in sessions with Public web access (/web)",
    ]);
  });

  it("a second up while one runs says so; a failed start shows its reason and hint", async () => {
    fakeBridge({ "search_server.up": { accepted: false, state: "starting" } });
    await dispatchSlashCommand("/search-server up");
    expect(notices().at(-1)).toBe("search server: a start is already running");
    expect(
      searchServerEventText({ phase: "error", state: "failed", reason: "pull x: unauthorized", hint: "podman logout docker.io" }),
    ).toBe("⚠ search server not started — pull x: unauthorized; podman logout docker.io");
    // 2026-11-01 live report: a stopped podman machine reads as one sentence
    // with the command that fixes it.
    expect(
      searchServerEventText({
        phase: "error",
        state: "failed",
        reason: "podman cannot reach its machine or service",
        hint: "start it with `podman machine start` (first time: `podman machine init`), then try again",
      }),
    ).toBe(
      "⚠ search server not started — podman cannot reach its machine or service; " +
        "start it with `podman machine start` (first time: `podman machine init`), then try again",
    );
  });

  it("down stops it; an unknown verb answers with the usage", async () => {
    const calls = fakeBridge({ "search_server.down": { state: "stopped" } });
    await dispatchSlashCommand("/search-server down");
    // The web chip re-reads the status after a stop (2026-11-03).
    expect(calls.map((c) => c.method)).toEqual(["search_server.down", "search_server.status"]);
    expect(notices().at(-1)).toContain("search server: stopped");
    await dispatchSlashCommand("/search-server sideways");
    expect(calls).toHaveLength(2);
  });

  it("the bridge line routes to the search_server_event channel", () => {
    expect(routeBridgeLine({ search_server_event: { phase: "progress", message: "m" } })).toEqual({
      kind: "notification",
      channel: "search_server_event",
      payload: { phase: "progress", message: "m" },
    });
  });
});

describe("the offer after /web Public", () => {
  it("opens instead of the plain notice when the bridge offers and the session is local", async () => {
    fakeBridge({
      "session.web_access_set": { web_access: PUBLIC_WIRE, notice: "needs a server", search_server_offer: true },
    });
    expect(await setSessionWebAccess({ mode: "public", domains: [] })).toBe(true);
    await tick();
    expect(workspace.searchServerOffer).toBe(true);
    expect(notices()).not.toContain("needs a server");
  });

  it("a remote session, or no engine, gets the notice only", async () => {
    setScopedField("sessionVenue", "frontend");
    fakeBridge({
      "session.web_access_set": { web_access: PUBLIC_WIRE, notice: "needs a server", search_server_offer: true },
    });
    await setSessionWebAccess({ mode: "public", domains: [] });
    await tick();
    expect(workspace.searchServerOffer).toBe(false);
    expect(notices()).toContain("needs a server");
  });

  it("Enter starts the local search server and closes the offer", async () => {
    const calls = fakeBridge({ "search_server.up": { accepted: true, state: "starting" } });
    actions.openSearchServerOffer();
    const setup = await testRender(() => <SearchServerOfferOverlay />, { width: 100, height: 30 });
    await settle(setup, 100);
    expect(setup.captureCharFrame()).toContain("Start a local one now?");
    setup.mockInput.pressEnter();
    await settle(setup, 150);
    expect(workspace.searchServerOffer).toBe(false);
    expect(calls.map((c) => c.method)).toEqual(["search_server.up"]);
    setup.renderer.destroy();
  });

  it("Esc declines: nothing starts, one line says how to start it later", async () => {
    const calls = fakeBridge({});
    actions.openSearchServerOffer();
    actions.declineSearchServerOffer();
    expect(workspace.searchServerOffer).toBe(false);
    expect(calls).toHaveLength(0);
    expect(notices().at(-1)).toContain("/search-server up starts it any time");
  });

  it("a failed request after Enter is shown, not lost", async () => {
    fakeBridge({ "search_server.up": new Error("bridge gone") });
    actions.openSearchServerOffer();
    await acceptSearchServerOffer();
    expect(notices().at(-1)).toContain("search server not started — bridge gone");
  });

  it("the overlay sweep closes the offer", () => {
    actions.openSearchServerOffer();
    actions.closeAllOverlays();
    expect(workspace.searchServerOffer).toBe(false);
  });
});
