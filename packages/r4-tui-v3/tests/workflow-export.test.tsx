/**
 * Workflow export in the console (2026-11-07): collaborators who see a
 * workflow through a project share can save it as a verified zip, from the
 * definition card (`e`) and from `/workflow export NAME`. One bridge verb
 * (`workflow.export`) does the work; the console shows the saved path and
 * counts in a notice, or the bridge's refusal in an error card.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { reconcile } from "solid-js/store";
import { dispatchSlashCommand, resetSlashCatalogCache } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { WorkflowDefOverlay } from "../src/components/workflow-def-overlay";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { setUmrsWorkflowStore } from "../src/stores/umrs-workflow";
import { setDemoMode } from "../src/lib/console-mode";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { WORKFLOW_EXPORT_TIMEOUT_MS, exportNotice, exportWorkflowWithNotice } from "../src/lib/workflow-export";

interface Call {
  method: string;
  params: Record<string, unknown>;
  timeoutMs?: number;
}

const SAVED = {
  ok: true,
  workflow_name: "vision-pipe",
  path: "/home/alice/work/vision-pipe-1.zip",
  files: 3,
  excluded: 1,
};

/** A bridge that answers workflow.export with `answer` (an Error rejects). */
function installBridge(answer: unknown = SAVED): Call[] {
  const calls: Call[] = [];
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>, timeoutMs?: number) => {
      calls.push({ method, params, timeoutMs });
      if (method !== "workflow.export") return {};
      if (answer instanceof Error) throw answer;
      return answer;
    },
  } as never);
  return calls;
}

function exportCalls(calls: Call[]): Call[] {
  return calls.filter((c) => c.method === "workflow.export");
}

function lastMessage(): { content: string; errorCard?: { what: string; detail?: string } } {
  return workspace.chatMessages.at(-1) as never;
}

const savedBackend = process.env["R4_TUI_BACKEND"];

beforeAll(() => resetSlashCatalogCache());

afterAll(() => {
  resetSlashCatalogCache();
  if (savedBackend === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackend;
});

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  resetWorkspace();
  actions.closeAllOverlays();
  setUmrsWorkflowStore("definitions", reconcile({}));
  setUmrsWorkflowStore("definitionErrors", reconcile({}));
});

afterEach(async () => {
  await Bun.sleep(5);
  setCompanionBridgeForTests(null);
  setDemoMode(false);
  actions.setWorkflowDefId(null);
  resetWorkspace();
});

describe("exportNotice", () => {
  test("names the workflow, the saved path and both counts", () => {
    expect(exportNotice(SAVED as never)).toBe(
      "exported workflow vision-pipe → /home/alice/work/vision-pipe-1.zip (3 files, 1 excluded) · container images not included",
    );
  });

  test("leaves out counts the bridge could not read back", () => {
    const text = exportNotice({ ...SAVED, files: null, excluded: null } as never);
    expect(text).toBe("exported workflow vision-pipe → /home/alice/work/vision-pipe-1.zip · container images not included");
  });
});

describe("/workflow export NAME", () => {
  test("calls workflow.export with the name and a long timeout, then shows the saved path", async () => {
    const calls = installBridge();
    const result = await dispatchSlashCommand("/workflow export vision-pipe");
    expect(result).toEqual({ kind: "handled" });
    expect(exportCalls(calls)).toEqual([
      { method: "workflow.export", params: { workflow_name: "vision-pipe" }, timeoutMs: WORKFLOW_EXPORT_TIMEOUT_MS },
    ]);
    expect(lastMessage().content).toContain("→ /home/alice/work/vision-pipe-1.zip (3 files, 1 excluded)");
  });

  test("without a name the composer gets the verb to complete; nothing is requested", async () => {
    const calls = installBridge();
    expect(await dispatchSlashCommand("/workflow export")).toEqual({ kind: "insert", text: "/workflow export " });
    expect(exportCalls(calls)).toEqual([]);
  });

  test("a refusal (not visible to the caller) becomes the workflow error card", async () => {
    installBridge(new Error("workflow 'secret' not found"));
    const result = await dispatchSlashCommand("/workflow export secret");
    expect(result).toEqual({ kind: "error", message: "workflow 'secret' not found" });
    expect(lastMessage().errorCard?.what).toBe("workflow export failed");
    expect(lastMessage().errorCard?.detail).toBe("workflow 'secret' not found");
  });
});

describe("exportWorkflowWithNotice (definition card)", () => {
  test("offline mode refuses by name without touching the bridge", async () => {
    const calls = installBridge();
    setDemoMode(true);
    expect(await exportWorkflowWithNotice("vision-pipe")).toBe(false);
    expect(exportCalls(calls)).toEqual([]);
    expect(lastMessage().errorCard?.detail).toContain("live backend");
  });

  test("an error never throws: it lands in an error card naming the workflow", async () => {
    installBridge(new Error("413 export too large"));
    expect(await exportWorkflowWithNotice("big")).toBe(false);
    expect(lastMessage().errorCard?.what).toBe("workflow export big failed");
    expect(lastMessage().errorCard?.detail).toBe("413 export too large");
  });
});

describe("WorkflowDefOverlay `e`", () => {
  const DEF = { workflow_name: "vision-pipe", steps: [], files: [] } as never;

  async function openCard() {
    setUmrsWorkflowStore("definitions", "vision-pipe", DEF);
    actions.setWorkflowDefId("vision-pipe");
    const setup = await testRender(() => <WorkflowDefOverlay />, { width: 120, height: 30 });
    await setup.renderOnce();
    return setup;
  }

  test("the footer advertises the key", async () => {
    installBridge();
    const setup = await openCard();
    expect(setup.captureCharFrame()).toContain("e export zip");
    setup.renderer.destroy();
  });

  test("e exports the open workflow and keeps the card open", async () => {
    const calls = installBridge();
    const setup = await openCard();
    setup.mockInput.pressKey("e");
    await Bun.sleep(5);
    await setup.renderOnce();
    expect(exportCalls(calls).map((c) => c.params)).toEqual([{ workflow_name: "vision-pipe" }]);
    expect(workspace.workflowDefId).toBe("vision-pipe");
    expect(lastMessage().content).toContain("exported workflow vision-pipe →");
    setup.renderer.destroy();
  });

  test("Ctrl+E is not the export key", async () => {
    const calls = installBridge();
    const setup = await openCard();
    setup.mockInput.pressKey("e", { ctrl: true });
    await Bun.sleep(5);
    expect(exportCalls(calls)).toEqual([]);
    setup.renderer.destroy();
  });
});
