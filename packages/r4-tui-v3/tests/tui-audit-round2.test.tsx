/**
 * TUI audit 2026-10-17, round 2 — v3 halves of fixes whose bridge/companion
 * side landed in round 1: /rag answer rendering (#38), the retired
 * /admin rag-profiles (#84), /sessions turns rows (#58/#66/#75/#86), the
 * revert notice for a created file (#68) and the /endpoint down picker (#13).
 */
import { describe, test, expect, beforeEach, afterAll } from "bun:test";
import { testRender } from "@opentui/solid";
import { dispatchSlashCommand } from "../src/commands/slash";
import { resolveSlashPicker } from "../src/commands/slash-pickers";
import { sessionTurnLine } from "../src/bridge/session-bridge";
import { ListOverlay, revertNoticeText } from "../src/components/list-overlay";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, workspace, resetWorkspace, setWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import type { UiEndpoint } from "../src/types";

const savedBackendEnv = process.env["R4_TUI_BACKEND"];
afterAll(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
});

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge(replies: Record<string, unknown>) {
  const calls: Call[] = [];
  const bridge = {
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      const reply = replies[method];
      if (reply instanceof Error) throw reply;
      return reply ?? {};
    },
  };
  return { calls, bridge };
}

async function run(line: string, replies: Record<string, unknown> = {}) {
  const { calls, bridge } = fakeBridge(replies);
  setCompanionBridgeForTests(bridge as never);
  try {
    return { result: await dispatchSlashCommand(line), calls };
  } finally {
    setCompanionBridgeForTests(null);
  }
}

function lastText(): string {
  return workspace.chatMessages.at(-1)?.content ?? "";
}

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("/rag <question>", () => {
  // RAGQueryResponse (umrs/api/rag.py) + SpineAnswer.metadata (rag_spine.py).
  const answer = {
    answer: "MOF-5 has a BET surface of about 3800 m2/g [1].",
    sources: [],
    metadata: {
      citations: [{ label: "[1]", source_id: "doc-7" }],
      abstained: false,
    },
  };

  test("defaults project_id to the active project", async () => {
    setWorkspace("project", { id: "p-active" } as never);
    const { calls } = await run("/rag what is MOF-5", { "rag.query": answer });
    const call = calls.find((c) => c.method === "rag.query");
    expect(call?.params).toEqual({ query: "what is MOF-5", project_id: "p-active" });
  });

  test("an explicit project= wins over the active project", async () => {
    setWorkspace("project", { id: "p-active" } as never);
    const { calls } = await run("/rag what is MOF-5 project=p-other", { "rag.query": answer });
    expect(calls.find((c) => c.method === "rag.query")?.params.project_id).toBe("p-other");
  });

  test("renders metadata.citations", async () => {
    await run("/rag what is MOF-5", { "rag.query": answer });
    const text = lastText();
    expect(text).toContain("3800 m2/g");
    expect(text).toContain("citations (1):");
    expect(text).toContain("[1] doc-7");
  });

  test("renders the abstained state", async () => {
    await run("/rag what is unobtainium", {
      "rag.query": { answer: "The sources do not say.", sources: [], metadata: { citations: [], abstained: true } },
    });
    const text = lastText();
    expect(text).toContain("abstained");
    expect(text).toContain("The sources do not say.");
  });
});

describe("/admin rag-profiles (retired upstream: 410 Gone)", () => {
  test("is an unknown sub and calls nothing", async () => {
    const { result, calls } = await run("/admin rag-profiles");
    expect(calls.map((c) => c.method)).not.toContain("admin.rag_profiles");
    expect(result.kind).toBe("error");
    if (result.kind === "error") expect(result.message).not.toContain("rag-profiles");
  });

  test("the usage notice no longer advertises it", async () => {
    await run("/admin");
    expect(lastText()).toContain("rag-stats");
    expect(lastText()).not.toContain("rag-profiles");
  });
});

describe("/admin memory (retired: no gateway location for reflective memory)", () => {
  test("is an unknown sub and calls nothing", async () => {
    const { result, calls } = await run("/admin memory outcome");
    expect(calls.map((c) => c.method)).not.toContain("admin.memory_inspect");
    expect(result.kind).toBe("error");
  });

  test("the usage notice no longer advertises it", async () => {
    await run("/admin");
    expect(lastText()).not.toContain("/admin memory");
  });
});

describe("/sessions turns <id>", () => {
  // companion_sessions._turn_entries: turn_id, index, role, committed_at.
  const entry = { turn_id: "t-1", index: 3, role: "user", committed_at: "2026-10-17T09:00:00" };

  test("renders index, role and committed_at", async () => {
    await run("/sessions turns sess-abcdef12", { "session.turns": { rows: [entry], count: 1 } });
    const text = lastText();
    expect(text).toContain("turns (1)");
    expect(text).toContain("#3 t-1 · user · 2026-10-17T09:00:00");
  });

  test("also reads the rows under 'turns'", async () => {
    await run("/sessions turns sess-abcdef12", { "session.turns": { turns: [entry] } });
    expect(lastText()).toContain("#3 t-1 · user");
  });

  test("a row without index still shows the id", () => {
    expect(sessionTurnLine({ turn_id: "t-2", role: "assistant" })).toBe("  t-2 · assistant");
  });
});

describe("changes overlay revert notice", () => {
  test("names the deletion when the session created the file", () => {
    expect(revertNoticeText("new.txt", { reverted: true, deleted: true })).toBe(
      "removed new.txt (created this session)",
    );
    expect(revertNoticeText("a.txt", { reverted: true, bytes: 3 })).toBe(
      "reverted a.txt → session before-state",
    );
  });

  test("r arms, R reverts, and a created file reports the removal", async () => {
    const { calls, bridge } = fakeBridge({
      get_session_changes: { changes: [{ path: "new.txt", before: "", after: "x", edits: 1 }] },
      revert_session_change: { path: "new.txt", reverted: true, bytes: 0, deleted: true },
    });
    setCompanionBridgeForTests(bridge as never);
    try {
      actions.openListOverlay("changes");
      const setup = await testRender(() => <ListOverlay />, { width: 120, height: 30 });
      await setup.renderOnce();
      await new Promise((r) => setTimeout(r, 50));
      await setup.renderOnce();
      setup.mockInput.pressKey("r" as never);
      await setup.renderOnce();
      // R (the terminal delivers name "r" + shift) — it used to disarm.
      setup.mockInput.pressKey("R" as never);
      await new Promise((r) => setTimeout(r, 50));
      await setup.renderOnce();
      expect(calls.find((c) => c.method === "revert_session_change")?.params).toEqual({ path: "new.txt" });
      expect(workspace.chatMessages.map((m) => m.content)).toContain("removed new.txt (created this session)");
      setup.renderer.destroy();
    } finally {
      setCompanionBridgeForTests(null);
    }
  });
});

describe("/endpoint down picker", () => {
  function endpoint(id: string, status: UiEndpoint["status"]): UiEndpoint {
    return {
      id,
      model: `m-${id}`,
      servedModelName: id,
      status,
      url: "",
      tier: "user",
      backend: "vllm",
      host: "node1",
      port: 8000,
      createdAt: "2026-10-17T00:00:00Z",
    };
  }

  test("dispatches the instance id, keeping [N] only in the label", async () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    setWorkspace("endpoints", [endpoint("inst-a", "provisioning"), endpoint("inst-b", "ready")]);
    const picker = await resolveSlashPicker("/endpoint down");
    expect(picker?.commandPrefix).toBe("/endpoint down ");
    expect(picker?.items.map((i) => i.key)).toEqual(["inst-a", "inst-b"]);
    expect(picker?.items[1]!.label.startsWith("[2] ")).toBe(true);
  });

  test("the fallback filter matches the companion's terminal statuses", async () => {
    process.env["R4_TUI_BACKEND"] = "offline";
    setWorkspace("endpoints", [endpoint("inst-s", "stopped"), endpoint("inst-f", "failed")]);
    const picker = await resolveSlashPicker("/endpoint down");
    expect(picker?.items.map((i) => i.key)).toEqual(["inst-s"]);
  });
});
