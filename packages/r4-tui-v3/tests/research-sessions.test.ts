/**
 * W10 — Research Sessions surface in the Bun console.
 *
 * Mirrors the other adapters smoke coverage: mapper normalisation, the
 * offline fixture having the same shape the route renders, and the store
 * round-trip. The live (r4-cli) path is smoke-tested locally via the
 * knowledge-adapter listSessions action shape.
 */

import { describe, expect, test } from "bun:test";
import { mapSessionRow } from "../src/adapters/knowledge-mappers";
import { OfflineKnowledgeAdapter } from "../src/adapters/offline";
import { OfflineDataSource } from "../src/adapters/offline";
import { knowledgeActions, knowledgeStore } from "../src/stores/knowledge";

describe("sessions mapper", () => {
  test("fully populated server row → console shape", () => {
    const sess = mapSessionRow({
      session_id: "sess-1",
      project_id: "proj-1",
      title: "MERMAID filter study",
      research_goal: "goal text",
      status: "paused",
      authoritative_runtime: "hybrid",
      created_at: "2026-08-06",
      updated_at: "2026-08-06",
      loops: [{ loop_id: "loop-1", venue: "umrs", loop_role: "auto", attached_at: "x" }],
    });
    expect(sess.sessionId).toBe("sess-1");
    expect(sess.status).toBe("paused");
    expect(sess.authoritativeRuntime).toBe("hybrid");
    expect(sess.loops).toHaveLength(1);
    expect(sess.loops![0].venue).toBe("umrs");
    expect(sess.archivedAt).toBeNull();
  });

  test("missing fields default safely", () => {
    const sess = mapSessionRow({ session_id: "sess-x" });
    expect(sess.sessionId).toBe("sess-x");
    expect(sess.title).toBe("");
    expect(sess.status).toBe("active");
    expect(sess.authoritativeRuntime).toBe("umrs");
    expect(sess.loops).toEqual([]);
  });
});

describe("offline fixture", () => {
  test("listSessions returns one populated session", async () => {
    const adapter = new OfflineKnowledgeAdapter();
    const data = await adapter.listSessions("proj-1");
    expect((data.sessions as unknown[]).length).toBeGreaterThan(0);
    const sess = (data.sessions as Record<string, unknown>[])[0];
    expect(sess.session_id).toBe("sess-1");
    expect(sess.status).toBe("active");
  });

  test("createSession returns session payload", async () => {
    const adapter = new OfflineKnowledgeAdapter();
    const data = await adapter.createSession("proj-1", "MERMAID filter for peaks");
    const sess = data.session as Record<string, unknown>;
    expect(String(sess.session_id)).toContain("sess-gen-");
    expect(sess.status).toBe("active");
    expect(String(sess.title)).toContain("MERMAID");
  });

  test("pollProjectKnowledge includes sessions", async () => {
    const source = new OfflineDataSource();
    const result = await source.pollProjectKnowledge("proj-1");
    expect(result.sessions).not.toBeNull();
    expect(result.sessions!).toHaveLength(1);
    expect(result.sessions![0].sessionId).toBe("sess-1");
  });
});

describe("knowledge store", () => {
  test("setSessions round-trips through the store", () => {
    const sess = mapSessionRow({
      session_id: "sess-store",
      project_id: "proj-1",
      status: "active",
      created_at: "x",
      updated_at: "x",
    });
    knowledgeActions.setSessions([sess]);
    expect(knowledgeStore.sessions).toHaveLength(1);
    expect(knowledgeStore.sessions![0].sessionId).toBe("sess-store");
    knowledgeActions.setSessions(null);
    expect(knowledgeStore.sessions).toBeNull();
  });
});
