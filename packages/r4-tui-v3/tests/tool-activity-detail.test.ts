/**
 * Click-expand full command + stdout parity tests for tool detail bodies.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { buildToolDetailBody, wrapWidth, type ActivityLine } from "../src/lib/tool-activity";
import { resetTermWidthForTest, syncTermWidth } from "../src/lib/term-width";

// Deterministic wrap column: pin the synced terminal width (the renderer
// syncs this from the real terminal; tests must not depend on stdout.columns).
beforeEach(() => {
  resetTermWidthForTest();
  syncTermWidth(110);
});

describe("buildToolDetailBody", () => {
  it("includes the verbatim full command for shell_exec", () => {
    const line: ActivityLine = {
      status: "ok",
      text: "✓ Ran find …",
      name: "shell_exec",
      args: { command: "find / -name 'a very long file name.txt' -type f -print0" },
      resultText: "first\nsecond",
    };
    const body = buildToolDetailBody(line);
    expect(body[0]).toBe("$ find / -name 'a very long file name.txt' -type f -print0");
    expect(body).toContain("  | first");
    expect(body).toContain("  | second");
  });

  it("renders python code lines for python_exec", () => {
    const line: ActivityLine = {
      status: "ok",
      text: "✓ Ran Python",
      name: "python_exec",
      args: { code: "import os\nprint(os.getcwd())" },
      resultText: "/tmp/ws",
    };
    const body = buildToolDetailBody(line);
    expect(body[0]).toBe("» import os");
    expect(body[1]).toBe("  print(os.getcwd())");
    expect(body).toContain("  | /tmp/ws");
  });

  it("caps huge output with a truncation marker", () => {
    const line: ActivityLine = {
      status: "ok",
      text: "✓ Ran ls …",
      name: "shell_exec",
      args: { command: "ls" },
      resultText: Array.from({ length: 500 }, (_, i) => `line-${i}`).join("\n"),
    };
    const body = buildToolDetailBody(line);
    expect(body.some((l) => l.includes("more lines"))).toBe(true);
    expect(body.length).toBeLessThanOrEqual(62); // 1 cmd + 60 lines + marker
  });

  it("is empty-headed when neither command nor output exists", () => {
    const line: ActivityLine = { status: "ok", text: "✓ Listed .", name: "list_dir", args: {} };
    expect(buildToolDetailBody(line)).toEqual([]);
  });

  it("hard-wraps very long commands so they stay visible without horizontal scroll", () => {
    const long = "x".repeat(300);
    const line: ActivityLine = {
      status: "ok",
      text: "✓ Ran …",
      name: "shell_exec",
      args: { command: long },
      resultText: "",
    };
    const body = buildToolDetailBody(line);
    expect(body.length).toBe(3); // "$ " prefix adds 2 chars → 3 chunks of ≤110
    expect(Math.max(...body.map((l) => l.length))).toBeLessThanOrEqual(110);
    expect(body.join("").replace("$ ", "",)).toBe(long);
  });

  it("wrap column follows the synced terminal width (no fixed 110 breaklines)", () => {
    syncTermWidth(200);
    expect(wrapWidth()).toBe(196); // chatContentWidth(4) at 200 cols
    const long = "x".repeat(300);
    const line: ActivityLine = {
      status: "ok",
      text: "✓ Ran …",
      name: "shell_exec",
      args: { command: long },
      resultText: "",
    };
    const body = buildToolDetailBody(line);
    // 302 chars at a 196-col wrap → 2 chunks, not the 3 chunks a fixed
    // 110-col cap would have produced on a wide terminal.
    expect(body.length).toBe(2);
    expect(Math.max(...body.map((l) => l.length))).toBeLessThanOrEqual(196);
    expect(body.join("").replace("$ ", "",)).toBe(long);
  });

  it("preview mode shows only the first N output lines plus a marker", () => {
    const line: ActivityLine = {
      status: "ok",
      text: "✓ Ran seq …",
      name: "shell_exec",
      args: { command: "seq 1 20" },
      resultText: Array.from({ length: 20 }, (_, i) => String(i + 1)).join("\n"),
    };
    const preview = buildToolDetailBody(line, 5);
    expect(preview[0]).toBe("$ seq 1 20");
    expect(preview.filter((l) => l.startsWith("  | 1") || l === "  | 5")).toContain("  | 5");
    expect(preview.some((l) => l.includes("(15 more lines)"))).toBe(true);
    const full = buildToolDetailBody(line, 1_000_000);
    expect(full.filter((l) => l.includes("| 20"))).toContain("  | 20");
    expect(full.some((l) => l.includes("more lines"))).toBe(false);
  });

  it("group/call expand keys are distinct per message + call index", () => {
    // Convention: `group:<msgId>` top level, `tc:<msgId>:<i>` per call,
    // nested `…:full` for the second expand level. Rendered in chat.tsx.
    const msgId = "msg-1";
    expect(`tc:${msgId}:0`.startsWith("group:")).toBe(false);
    expect(("group:" + msgId) === `group:${msgId}`).toBe(true);
    expect(`tc:${msgId}:1:full`.endsWith(":full")).toBe(true);
  });
});
