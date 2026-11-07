/**
 * K1.3 one listing truth (knowledge plan 2026-10-24): the Knowledge pane
 * lists the server's `/k2/artifacts` rows (the same list as
 * `r4 knowledge ls`), with who added each one. It read a local
 * artifact_manifest.json that a durable import never wrote, so the pane
 * stayed empty after every successful upload (F3), and its empty hint sent
 * people to `/project import → init`, which dead-ends (F2).
 *
 * The adapter runs against a fake interpreter (R4_TUI_PYTHON) whose answer
 * comes from FAKE_KNOWLEDGE (the tui-r2-hooks-cli-adapter pattern).
 */
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "bun:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { testRender } from "@opentui/solid";
import { R4CliDataSource } from "../src/adapters/r4-cli";
import { mapArtifactsToKnowledgeObjects, knowledgeRowLabel } from "../src/adapters/knowledge-mappers";
import { KNOWLEDGE_EMPTY, BLACKBOARD_EMPTY, READINESS_EMPTY, STRATEGY_EMPTY } from "../src/lib/empty-hints";
import { applyProjectKnowledgePoll } from "../src/hooks/useKnowledge";
import { knowledgeStore } from "../src/stores/knowledge";
import { resetResolvedPythonCache } from "../src/lib/resolve-python";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { KnowledgeRoute } from "../src/routes/knowledge";
import { ChatPanel } from "../src/components/chat";

const ARTIFACT = {
  artifact_id: "art-0123",
  kind: "document",
  embedding_status: "pending",
  metadata: { source_name: "fallback.md" },
  last_added: "2026-10-24T09:30:00+00:00",
  contributions: [
    { graph_id: "g-default", user_id: "alice", origin_actor: "user", title: "mof-notes.md" },
    { graph_id: "g-methods", user_id: "bob", origin_actor: "user", title: "" },
  ],
};

const SCRIPT = `#!/bin/sh
cat > /dev/null; eval "$FAKE_KNOWLEDGE"
`;

const saved: Record<string, string | undefined> = {};
let dir = "";

beforeAll(() => {
  for (const key of ["R4_TUI_PYTHON", "R4_PROJECT_ID", "FAKE_KNOWLEDGE"]) saved[key] = process.env[key];
  dir = mkdtempSync(join(tmpdir(), "k13-pane-"));
  const fake = join(dir, "fake-python");
  writeFileSync(fake, SCRIPT);
  chmodSync(fake, 0o755);
  process.env["R4_TUI_PYTHON"] = fake;
  resetResolvedPythonCache();
});

afterAll(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  resetResolvedPythonCache();
  rmSync(dir, { recursive: true, force: true });
});

beforeEach(() => {
  resetWorkspace();
});

describe("server rows", () => {
  it("map with their contributors, graphs and indexed state", () => {
    const [row] = mapArtifactsToKnowledgeObjects([ARTIFACT]);
    expect(row).toMatchObject({
      id: "art-0123",
      name: "mof-notes.md",
      status: "indexing",
      contributors: ["alice", "bob"],
      graphs: ["g-default", "g-methods"],
      kind: "document",
    });
    expect(knowledgeRowLabel(row!)).toBe("mof-notes.md · alice, bob");
    expect(mapArtifactsToKnowledgeObjects({ error: "x" })).toEqual([]);
  });

  it("the poll reads the server list, not a local manifest", async () => {
    // Nothing on disk for this project: only the server knows the upload.
    process.env["FAKE_KNOWLEDGE"] = `echo '${JSON.stringify({ artifacts: [ARTIFACT], sessions: [] })}'`;
    const result = await new R4CliDataSource().pollProjectKnowledge("k13-no-local-files");
    expect(result.knowledge.map((k) => [k.id, k.contributors])).toEqual([["art-0123", ["alice", "bob"]]]);
  });

  it("the pane renders a server row with who added it", async () => {
    const [row] = mapArtifactsToKnowledgeObjects([ARTIFACT]);
    setWorkspace("project", { id: "P1", name: "P1", description: "", status: "active", goalIds: [] } as never);
    actions.setKnowledge([row!]);
    actions.openPane("knowledge");
    const setup = await testRender(() => <KnowledgeRoute />, { width: 140, height: 40 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    setup.renderer.destroy();
    expect(frame).toContain("mof-notes.md · alice, bob");
    expect(frame).not.toContain(KNOWLEDGE_EMPTY.slice(0, 30));
  });
});

describe("empty hints", () => {
  it("never route through the dead-end /project import → init", () => {
    for (const hint of [KNOWLEDGE_EMPTY, BLACKBOARD_EMPTY]) {
      expect(hint).not.toContain("/project init");
      expect(hint).not.toContain("/project import");
    }
    expect(KNOWLEDGE_EMPTY).toContain("/rag upload");
  });
});

// Review of K1 part B (2026-10-24).
describe("review fixes", () => {
  const P1 = { id: "P1", name: "P1", description: "", status: "active", goalIds: [] };

  it("an unreadable server list is not an empty project", async () => {
    process.env["FAKE_KNOWLEDGE"] = `echo '${JSON.stringify({ error: "403 not a member", sessions: [] })}'`;
    const result = await new R4CliDataSource().pollProjectKnowledge("k13-error");
    expect(result.knowledgeError).toBe("403 not a member");
    const [row] = mapArtifactsToKnowledgeObjects([ARTIFACT]);
    setWorkspace("project", P1 as never);
    applyProjectKnowledgePoll({ ...result, knowledgeError: null, knowledge: [row!] }, "P1");
    applyProjectKnowledgePoll(result, "P1");
    expect(workspace.knowledge.map((k) => k.id)).toEqual(["art-0123"]); // kept, not wiped
    expect(knowledgeStore.listError).toBe("403 not a member");
    actions.setKnowledge([]);
    actions.openPane("knowledge");
    const setup = await testRender(() => <KnowledgeRoute />, { width: 140, height: 40 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    setup.renderer.destroy();
    expect(frame).toContain("knowledge list unavailable: 403 not a member");
    expect(frame).not.toContain(KNOWLEDGE_EMPTY.slice(0, 30));
    applyProjectKnowledgePoll({ ...result, knowledgeError: null, knowledge: [] }, "P1");
    expect(knowledgeStore.listError).toBeNull();
  });

  it("server rows count as ingested in the build strip", async () => {
    const [row] = mapArtifactsToKnowledgeObjects([ARTIFACT]);
    setWorkspace("project", P1 as never);
    actions.setKnowledge([row!]);
    actions.openPane("knowledge");
    const setup = await testRender(() => <KnowledgeRoute />, { width: 160, height: 40 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    setup.renderer.destroy();
    expect(frame).toContain("✓ ingest");
  });

  it("rows mark how each person added an item; unknown states are not ready", () => {
    const contributions = [
      { graph_id: "g", user_id: "alice", origin_actor: "agent_authored_user_kept" },
      { graph_id: "g", user_id: "bob", origin_actor: "agent" },
      { graph_id: "g", user_id: "mika", origin_actor: "agent_authored_user_vouched" },
    ];
    const [row] = mapArtifactsToKnowledgeObjects([{ ...ARTIFACT, embedding_status: "", contributions }]);
    expect(knowledgeRowLabel(row!)).toEndWith(" · alice (kept), bob (agent), mika (vouched)");
    expect(row!.status).toBe("indexing");
    const [failed] = mapArtifactsToKnowledgeObjects([{ ...ARTIFACT, embedding_status: "failed" }]);
    expect(failed!.status).toBe("error");
  });

  it("no hint left points at /project init", () => {
    for (const hint of [STRATEGY_EMPTY, READINESS_EMPTY]) expect(hint).not.toContain("init");
  });
});

describe("K1.6 review: the save key is visible", () => {
  it("the focused row's footer names s save and the turn number", async () => {
    const at = new Date().toISOString();
    setWorkspace("chatMessages", [
      { id: "u1", role: "user", content: "How much CO2?", timestamp: at, turnId: "t2" },
      { id: "a1", role: "assistant", content: "0.8 mmol/g", timestamp: at },
    ] as never);
    setWorkspace("messageCursor", 1);
    const setup = await testRender(() => <ChatPanel />, { width: 140, height: 40 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    setup.renderer.destroy();
    expect(frame).toContain("c copy · s save t2 · Esc back");
  });
});
