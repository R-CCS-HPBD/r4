/**
 * TUI audit 2026-10-17 (#0/#28/#36) — the Python registry aliases whose
 * canonical v3 row runs locally (/kb, /resume, /ckpt, /q) route exactly like
 * the canonical command, args included. They used to come back as
 * "Unknown slash command"; exporting them from the bridge alone would have
 * run the local row and dropped the args.
 */
import { describe, test, expect, beforeEach, afterAll } from "bun:test";
import {
  canonicalizeSlashAlias,
  dispatchSlashCommand,
  exactCatalogEntry,
  localSlashCommands,
} from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { paletteDirectLine } from "../src/lib/slash-palette";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

const savedBackendEnv = process.env["R4_TUI_BACKEND"];
afterAll(() => {
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
});

type Call = { method: string; params: Record<string, unknown> };

async function run(line: string, replies: Record<string, unknown> = {}) {
  const calls: Call[] = [];
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return replies[method] ?? {};
    },
  } as never);
  try {
    return { result: await dispatchSlashCommand(line), calls };
  } finally {
    setCompanionBridgeForTests(null);
  }
}

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("/kb is /knowledge", () => {
  test("/kb <query> runs the companion knowledge query with the args", async () => {
    const { result, calls } = await run("/kb MOF-5 porosity", {
      dispatch_slash_command: { handled: true, output: ["hits: 2"] },
    });
    expect(result.kind).toBe("handled");
    const sent = calls.find((c) => c.method === "dispatch_slash_command");
    expect(sent?.params).toEqual({ command: "/knowledge MOF-5 porosity" });
  });

  test("bare /kb is the knowledge status card", async () => {
    const { calls } = await run("/kb", {
      "research_knowledge.status": { available: true, nodes: 3, edges: 1 },
    });
    expect(calls.map((c) => c.method)).toEqual(["research_knowledge.status"]);
    expect(workspace.chatMessages.at(-1)?.mgmtCard?.type).toBe("knowledge-status");
  });
});

test("/ckpt list is /checkpoint list", async () => {
  const { calls } = await run("/ckpt list", {
    list_checkpoints: { checkpoints: [{ id: "cp-9", timestamp: "2026-10-17T10:00:00" }] },
  });
  expect(calls.map((c) => c.method)).toEqual(["list_checkpoints"]);
  expect(workspace.chatMessages.at(-1)?.content).toContain("cp-9");
});

test("/resume <id> opens the session like /sessions resume <id>", async () => {
  const opened: string[] = [];
  const original = actions.tabOpenSession;
  actions.tabOpenSession = (async (id: string) => {
    opened.push(id);
    return true;
  }) as typeof actions.tabOpenSession;
  try {
    const { result } = await run("/resume sess-abc");
    expect(result.kind).toBe("handled");
    expect(opened).toEqual(["sess-abc"]);
  } finally {
    actions.tabOpenSession = original;
  }
});

test("/q quits like /quit", async () => {
  let quits = 0;
  const original = actions.requestQuit;
  actions.requestQuit = (() => {
    quits += 1;
  }) as typeof actions.requestQuit;
  try {
    const { result } = await run("/q");
    expect(result.kind).toBe("handled");
    expect(quits).toBe(1);
  } finally {
    actions.requestQuit = original;
  }
});

describe("slash bar rows", () => {
  test("the local rows carry the aliases", () => {
    const rows = localSlashCommands();
    expect(exactCatalogEntry(rows, "kb")?.name).toBe("knowledge");
    expect(exactCatalogEntry(rows, "ckpt")?.name).toBe("checkpoint");
    expect(exactCatalogEntry(rows, "resume")?.name).toBe("sessions");
    expect(exactCatalogEntry(rows, "q")?.name).toBe("quit");
  });

  test("other names pass through unchanged", () => {
    const parsed = { name: "knowledge", args: "x" };
    expect(canonicalizeSlashAlias(parsed, "/knowledge x")).toEqual({ parsed, line: "/knowledge x" });
    expect(canonicalizeSlashAlias({ name: "resume", args: "" }, "/resume").line).toBe("/sessions");
  });
});

describe("command palette direct line", () => {
  test("resume <id> rewrites the args too, not just the name", () => {
    const rows = localSlashCommands();
    expect(paletteDirectLine("resume sess-abc", rows)).toBe("/sessions resume sess-abc");
    expect(paletteDirectLine("/kb MOF-5 porosity", rows)).toBe("/knowledge MOF-5 porosity");
    expect(paletteDirectLine("ckpt list", rows)).toBe("/checkpoint list");
  });

  test("the palette line for resume <id> opens the session", async () => {
    const opened: string[] = [];
    const original = actions.tabOpenSession;
    actions.tabOpenSession = (async (id: string) => {
      opened.push(id);
      return true;
    }) as typeof actions.tabOpenSession;
    try {
      const line = paletteDirectLine("resume sess-abc", localSlashCommands());
      const { result } = await run(line ?? "");
      expect(result.kind).toBe("handled");
      expect(opened).toEqual(["sess-abc"]);
    } finally {
      actions.tabOpenSession = original;
    }
  });
});

describe("inherited Object.prototype names are not aliases", () => {
  for (const name of ["constructor", "__proto__", "valueof", "hasownproperty"]) {
    test(`/${name} is an unknown command`, async () => {
      const { result } = await run(`/${name}`);
      expect(result).toEqual({ kind: "error", message: `Unknown slash command: /${name}` });
    });
  }

  test("/admin constructor is an unknown admin sub, with no bridge call", async () => {
    const { result, calls } = await run("/admin constructor");
    expect(result.kind).toBe("error");
    expect((result as { message: string }).message).toContain("admin: unknown sub");
    expect(calls).toEqual([]);
  });

  test("canonicalizeSlashAlias leaves them untouched", () => {
    const parsed = { name: "constructor", args: "" };
    expect(canonicalizeSlashAlias(parsed, "/constructor")).toEqual({ parsed, line: "/constructor" });
  });
});
