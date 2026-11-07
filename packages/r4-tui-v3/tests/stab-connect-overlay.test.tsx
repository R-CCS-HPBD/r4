/**
 * Stabilization lane `connect` (2026-10-21) — /connect and /source overlay pins.
 *
 *  connect#1   Log out showed a fixed "logged out" (even for r4's "Already
 *              logged out") and never reconnected the bridge, which kept
 *              sending the logged-out Bearer; the identity chip kept the user.
 *  connect#9   Add local Ollama said "added/updated" with no probe, so a dead
 *              endpoint made active looked fine.
 *  connect#7 / slash-b#4
 *              /source with no sources, or a failed `r4 source list`, showed
 *              the workflow catalog's message ("No workflows yet …").
 *  slash-b#2   /job <id> looked up the PREVIOUS id: the overlay mounted before
 *              the store set the target id.
 *
 * r4 is a fake interpreter (R4_TUI_PYTHON) answering per subcommand; the
 * bridge is a stub. No network, no real config.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { testRender } from "@opentui/solid";

import { ListOverlay } from "../src/components/list-overlay";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";
import { resetResolvedPythonCache } from "../src/lib/resolve-python";
import { actions, workspace } from "../src/stores";

const savedPython = process.env.R4_TUI_PYTHON;
const savedBackend = process.env.R4_TUI_BACKEND;
const dirs: string[] = [];

/** A fake r4: `answers` maps "<verb> <subverb>" to a shell body. */
function useFakeR4(answers: Record<string, string>): void {
  const dir = mkdtempSync(join(tmpdir(), "stab-connect-"));
  dirs.push(dir);
  const cases = Object.entries(answers)
    .map(([verb, body]) => `  "${verb}") ${body} ;;`)
    .join("\n");
  const python = join(dir, "python");
  writeFileSync(python, `#!/usr/bin/env bash\nshift 2\ncase "$1 $2" in\n${cases}\n  *) exit 0 ;;\nesac\n`);
  chmodSync(python, 0o755);
  process.env.R4_TUI_PYTHON = python;
  resetResolvedPythonCache();
}

interface BridgeLog {
  reconnects: number;
  requests: Array<{ method: string; params: Record<string, unknown> }>;
}

function stubBridge(answer: (method: string, params: Record<string, unknown>) => unknown): BridgeLog {
  const log: BridgeLog = { reconnects: 0, requests: [] };
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    reconnect: async () => {
      log.reconnects += 1;
    },
    request: async (method: string, params: Record<string, unknown> = {}) => {
      log.requests.push({ method, params });
      return answer(method, params);
    },
  } as never);
  return log;
}

function transcript(): string {
  return workspace.chatMessages.map((m) => m.content).join("\n");
}

async function settle(setup: { renderOnce: () => Promise<void> }, until: () => boolean): Promise<void> {
  for (let i = 0; i < 60 && !until(); i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 25));
    await setup.renderOnce();
  }
  await setup.renderOnce();
}

beforeEach(() => {
  delete process.env.R4_TUI_BACKEND;
  setDemoMode(false);
  actions.resetWorkspace();
});

afterEach(() => {
  actions.closeAllOverlays();
  setCompanionBridgeForTests(null);
  if (savedPython === undefined) delete process.env.R4_TUI_PYTHON;
  else process.env.R4_TUI_PYTHON = savedPython;
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
  resetResolvedPythonCache();
  for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

async function pressConnectRow(downs: number) {
  actions.openListOverlay("connect");
  const setup = await testRender(() => <ListOverlay />, { width: 140, height: 40 });
  await setup.renderOnce();
  for (let i = 0; i < downs; i += 1) {
    setup.mockInput.pressArrow("down");
    await setup.renderOnce();
  }
  setup.mockInput.pressEnter();
  return setup;
}

describe("/connect → Log out (connect#1)", () => {
  it("shows r4's own outcome, reconnects the bridge and clears the identity chip", async () => {
    useFakeR4({ "auth logout": `echo "✓ Logged out from profile: default"` });
    const bridge = stubBridge(() => ({}));
    actions.setIdentityState({ username: "verify-user", credential: "jwt" });
    const setup = await pressConnectRow(3);
    await settle(setup, () => bridge.reconnects > 0);
    expect(transcript()).toContain("✓ Logged out from profile: default");
    expect(bridge.reconnects).toBe(1);
    expect(workspace.identityUsername).toBe("");
    setup.renderer.destroy();
  });

  it("r4's 'Already logged out' is relayed, never turned into 'logged out'", async () => {
    useFakeR4({ "auth logout": `echo "ℹ Already logged out (profile: default)"` });
    stubBridge(() => ({}));
    const setup = await pressConnectRow(3);
    await settle(setup, () => transcript().includes("logged out"));
    expect(transcript()).toContain("Already logged out (profile: default)");
    setup.renderer.destroy();
  });
});

describe("/connect → Add local Ollama (connect#9)", () => {
  it("probes the new source and names a dead endpoint", async () => {
    useFakeR4({ "source add-ollama": `echo "Added source 'ollama' (public: http://localhost:11434)"` });
    stubBridge((method) =>
      method === "list_models"
        ? { models: [], problems: [{ source: "ollama", error: "unreachable (http://localhost:11434/v1)" }] }
        : {},
    );
    const setup = await pressConnectRow(1);
    await settle(setup, () => transcript().includes("ollama"));
    expect(transcript()).toContain("Added source 'ollama'");
    expect(transcript()).toContain("probe: unreachable (http://localhost:11434/v1)");
    expect(transcript()).toContain("ollama serve");
    setup.renderer.destroy();
  });
});

describe("/source overlay empty and failed states (connect#7, slash-b#4)", () => {
  async function openSources() {
    stubBridge(() => ({}));
    actions.openListOverlay("sources");
    const setup = await testRender(() => <ListOverlay />, { width: 160, height: 40 });
    await setup.renderOnce();
    return setup;
  }

  it("no sources → a sources message pointing at /connect, not the workflow catalog", async () => {
    useFakeR4({ "source list": `echo "[]"` });
    const setup = await openSources();
    await settle(setup, () => setup.captureCharFrame().includes("No sources"));
    const frame = setup.captureCharFrame();
    expect(frame).toContain("No sources registered");
    expect(frame).toContain("/connect");
    expect(frame).not.toContain("workflow");
    setup.renderer.destroy();
  });

  it("a failed `r4 source list` shows r4's error, not an empty state", async () => {
    useFakeR4({ "source list": `echo "Error: sources.json unreadable" >&2; exit 1` });
    const setup = await openSources();
    await settle(setup, () => setup.captureCharFrame().includes("failed"));
    const frame = setup.captureCharFrame();
    expect(frame).toContain("r4 source list failed: Error: sources.json unreadable");
    expect(frame).not.toContain("workflow");
    setup.renderer.destroy();
  });
});

describe("/job <id> overlay looks up its own id (slash-b#2)", () => {
  it("each open fetches the job named in its title", async () => {
    const bridge = stubBridge((method, params) => {
      if (method === "get_job") throw new Error(`job not found: ${String(params.job_id)}`);
      return {};
    });
    const setup = await testRender(() => <ListOverlay />, { width: 140, height: 40 });
    actions.openListOverlay("jobgraph", "aaa-111");
    await settle(setup, () => setup.captureCharFrame().includes("not found"));
    expect(setup.captureCharFrame()).toContain("job not found: aaa-111");

    actions.closeListOverlay();
    await setup.renderOnce();
    actions.openListOverlay("jobgraph", "bbb-222");
    await settle(setup, () => setup.captureCharFrame().includes("bbb-222 —") && setup.captureCharFrame().includes("not found"));
    expect(setup.captureCharFrame()).toContain("job not found: bbb-222");
    expect(setup.captureCharFrame()).not.toContain("aaa-111");
    expect(bridge.requests.filter((r) => r.method === "get_job").map((r) => r.params.job_id)).toEqual([
      "aaa-111",
      "bbb-222",
    ]);
    setup.renderer.destroy();
  });
});
