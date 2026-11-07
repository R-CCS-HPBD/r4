/**
 * TUI audit r2 (2026-10-17), hooks lane — #18: exporting a NON-active session
 * stamped the active session's model, cost and project into the file's
 * front-matter.
 */
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "bun:test";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { exportSession } from "../src/bridge/session-bridge";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import type { CompanionBridgeClient } from "../src/bridge/jsonl-client";
import { resetWorkspace, setWorkspace } from "../src/stores";

const cwd = process.cwd();
let dir = "";
let reply: Record<string, unknown> = {};

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "tui-r2-export-"));
  process.chdir(dir);
  setCompanionBridgeForTests({ request: async () => reply } as unknown as CompanionBridgeClient);
});

afterAll(() => {
  process.chdir(cwd);
  setCompanionBridgeForTests(null);
  rmSync(dir, { recursive: true, force: true });
});

beforeEach(() => {
  resetWorkspace();
  reply = { markdown: "# body\n" };
  setWorkspace("sessionId", "current-session-0001");
  setWorkspace("currentModel", "live-model");
  setWorkspace("project", { id: "LIVE_PROJ", name: "LIVE_PROJ", description: "", status: "active", goalIds: [] });
  setWorkspace("cost", { cost_usd_total: 9.5 } as never);
  setWorkspace("sessionsList", [
    { id: "older-session-0002", name: "old", model: "old-model", created_at: "", updated_at: "", cost_usd: 0.25 },
  ] as never);
});

describe("session export front-matter", () => {
  it("a non-active session never carries the live session's model, cost or project", async () => {
    const file = await exportSession("older-session-0002");
    const text = readFileSync(join(dir, file!), "utf8");
    expect(text).toContain("model: old-model");
    expect(text).toContain("cost_usd: 0.2500");
    expect(text).not.toContain("live-model");
    expect(text).not.toContain("LIVE_PROJ");
    expect(text).not.toContain("9.5000");
  });

  it("prefers the metadata the bridge returns for the exported session", async () => {
    reply = { markdown: "# body\n", model: "server-model", project: "OLD_PROJ", cost_usd: 1.5 };
    const file = await exportSession("older-session-0002");
    const text = readFileSync(join(dir, file!), "utf8");
    expect(text).toContain("model: server-model");
    expect(text).toContain("project: OLD_PROJ");
    expect(text).toContain("cost_usd: 1.5000");
  });

  it("the current session keeps the live values", async () => {
    const file = await exportSession();
    const text = readFileSync(join(dir, file!), "utf8");
    expect(text).toContain("model: live-model");
    expect(text).toContain("project: LIVE_PROJ");
  });

  it("a bare export names the live session: its own file and front-matter id (2026-10-21)", async () => {
    // tabs-sessions#6: every bare export wrote r4-session-current.md with
    // `session_id: current`, so a second tab's export overwrote the first.
    setWorkspace("sessionId", "abcd1234-0000-4000-8000-000000000001");
    const first = await exportSession();
    expect(first).toBe("r4-session-abcd1234.md");
    expect(readFileSync(join(dir, first!), "utf8")).toContain(
      "session_id: abcd1234-0000-4000-8000-000000000001",
    );
    setWorkspace("sessionId", "ef567890-0000-4000-8000-000000000002");
    const second = await exportSession();
    expect(second).toBe("r4-session-ef567890.md");
    expect(readFileSync(join(dir, first!), "utf8")).toContain("abcd1234-0000");
  });
});
