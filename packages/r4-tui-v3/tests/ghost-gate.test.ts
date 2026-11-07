/** T5 topic-19 tail pins: ghost-update gate evaluation. */
import { describe, expect, it } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { anyWriteToolRan, ghostGateVerdict, textClaimsWriteCompletion } from "../src/lib/ghost-gate";
import type { ActivityLine } from "../src/lib/tool-activity";

function lines(names: string[]): ActivityLine[] {
  return names.map((name) => ({ status: "ok", text: name, name }));
}

describe("ghost-gate verdict", () => {
  it("no write tools and no claim → clean", () => {
    const v = ghostGateVerdict(lines(["grep_search"]), "I examined the file structure");
    expect(v.verdict).toBe("clean");
    expect(v.writeToolCount).toBe(0);
  });

  it("write tools present regardless of claim → clean", () => {
    const v = ghostGateVerdict(lines(["file_write"]), "Done — updated.");
    expect(v.verdict).toBe("clean");
    expect(v.writeToolCount).toBe(1);
  });

  it("completion claim without any write tool → ghost-suspect", () => {
    const v = ghostGateVerdict(lines(["grep_search", "file_read"]), "Done — I updated the file.");
    expect(v.verdict).toBe("ghost-suspect");
    expect(v.reason).toBeTruthy();
  });

  it("reads (file_read/grep_search) never count as work evidence", () => {
    expect(anyWriteToolRan(lines(["file_read", "grep_search"]))).toBe(false);
    expect(anyWriteToolRan(lines(["shell_exec"]))).toBe(true);
    expect(anyWriteToolRan(lines(["file_edit"]))).toBe(true);
  });

  it("every state-changing tool counts, not only the file writers", () => {
    for (const name of ["apply_patch", "workflow_run", "workflow_upload", "knowledge_ingest", "nodejs_exec"]) {
      expect(anyWriteToolRan(lines([name]))).toBe(true);
    }
  });

  it("a denied or blocked write is not evidence", () => {
    const denied: ActivityLine[] = [{ status: "warn", blocked: true, text: "⛔ file_write", name: "file_write" }];
    expect(anyWriteToolRan(denied)).toBe(false);
    expect(ghostGateVerdict(denied, "I've updated config.yaml.").verdict).toBe("ghost-suspect");
  });
});

describe("ghost-gate claim phrasing", () => {
  it("first-person and passive-perfect change claims count", () => {
    for (const text of [
      "I've updated the config.",
      "I’ve added the test.",
      "I have now also fixed the import.",
      "We created two files.",
      "The changes have been applied.",
      "All done!",
    ]) {
      expect(textClaimsWriteCompletion(text)).toBe(true);
    }
  });

  it("read-only status answers and negations stay clean", () => {
    for (const text of [
      "Your run wf-123 completed successfully at 10:02.",
      "Nothing has changed since the last check.",
      "The file was last updated in 2024.",
      "I haven't changed anything yet.",
      "The config has not been modified.",
      "The job is done; results are in the bucket.",
      "Removing the cache would fix it — shall I?",
      "The update log shows progress",
      "Please write a recap then we read",
    ]) {
      expect(textClaimsWriteCompletion(text)).toBe(false);
    }
  });
});

describe("ghost-gate evidence parity with the companion permission set", () => {
  const permissions = join(import.meta.dir, "../../../r4/src/r4/companion/permissions.py");

  it.skipIf(!existsSync(permissions))("every RISKY_TOOLS entry counts as evidence", () => {
    const source = readFileSync(permissions, "utf8");
    const block = /RISKY_TOOLS: frozenset\[str\] = frozenset\(\s*\{([\s\S]*?)\}\s*\)/.exec(source);
    expect(block).not.toBeNull();
    const names = [...block![1]!.matchAll(/"([a-z0-9_]+)"/g)].map((m) => m[1]!);
    expect(names.length).toBeGreaterThan(10);
    expect(names.filter((name) => !anyWriteToolRan(lines([name])))).toEqual([]);
  });
});
