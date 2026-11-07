import { describe, expect, test } from "bun:test";
import {
  collapseToolActivity,
  formatTodoActivity,
  formatToolActivityEntry,
  humanizeToolDone,
  humanizeToolInProgress,
  summarizeToolResult,
  summarizeToolTurn,
  type ActivityLine,
} from "../src/lib/tool-activity";
import {
  formatWorkflowDone,
  formatWorkflowInProgress,
  isWorkflowError,
  isWorkflowTool,
  parseWorkflowPayload,
} from "../src/lib/workflow-activity";
import { argSummary, basename, formatPathLabel, parseToolArgs, patternHint } from "../src/lib/tool-args";
import { iconizeToolLabel, isDeniedText, toolGlyph } from "../src/lib/tool-glyph";
import { statusIcon } from "../src/lib/tool-icons";

describe("tool-args", () => {
  test("basename", () => {
    expect(basename("/a/b/c.py")).toBe("c.py");
    expect(basename("a\\b\\c.py")).toBe("c.py");
    expect(basename("")).toBe("");
  });
  test("parseToolArgs", () => {
    expect(parseToolArgs('{"path":"x"}')).toEqual({ path: "x" });
    expect(parseToolArgs("{bad json")).toEqual({});
    expect(parseToolArgs(null)).toEqual({});
  });
  test("argSummary shapes", () => {
    expect(argSummary({ path: "/a", pattern: "*.py" })).toBe('"*.py" in /a');
    expect(argSummary({ query: "q" })).toBe('"q"');
    expect(argSummary({})).toBe("");
  });
  test("patternHint + formatPathLabel", () => {
    expect(patternHint({ pattern: "src/**" })).toBe("src/**");
    expect(formatPathLabel(".")).toBe("project root");
    expect(formatPathLabel("/a/b")).toBe("b");
  });
});

describe("glyph sync with tui-js registry", () => {
  test("newly synced entries", () => {
    expect(toolGlyph("workflow_describe")).toBe("𝒊");
    expect(toolGlyph("hpc_submit")).toBe("⇑");
    expect(toolGlyph("pool_up")).toBe("≋↑");
    expect(toolGlyph("rag_query")).toBe("📚");
    expect(toolGlyph("agent_spawn")).toBe("♢");
    expect(toolGlyph("unknown_tool")).toBe("");
  });
  test("iconizeToolLabel", () => {
    expect(iconizeToolLabel("shell_exec", "Ran ls")).toBe("$ Ran ls");
    expect(iconizeToolLabel("nope", "plain")).toBe("plain");
  });
  test("isDeniedText union", () => {
    expect(isDeniedText("[tool_contract:permission_denied x]")).toBe(true);
    expect(isDeniedText("tool file_write blocked by policy")).toBe(true);
    expect(isDeniedText("allowed_tools_missing")).toBe(true);
    expect(isDeniedText("read 12 lines")).toBe(false);
  });
});

describe("todo + result summaries", () => {
  test("formatTodoActivity", () => {
    const body = formatTodoActivity(
      JSON.stringify([
        { content: "task a", status: "completed" },
        { content: "task b", status: "in_progress" },
        { content: "task c", status: "pending" },
        { content: "task d", status: "todo" },
      ]),
    );
    expect(body).toBe("Todos 1/4 done · ▶ task b · +1 more");
    expect(formatTodoActivity("not json")).toBeNull();
  });
  test("summarizeToolResult", () => {
    expect(summarizeToolResult("")).toBe("done");
    expect(summarizeToolResult("(no matches)")).toBe("no matches");
    expect(summarizeToolResult("a\nb\nc", "glob_search")).toBe("3 matches");
    expect(summarizeToolResult("a\nb\nc")).toBe("3 lines");
    expect(summarizeToolResult("short line")).toBe("short line");
  });
});

describe("humanized labels", () => {
  test("in progress", () => {
    expect(humanizeToolInProgress("file_read", { path: "/x/y.md" })).toBe("Reading y.md…");
    expect(humanizeToolInProgress("list_dir", { path: "." })).toBe("Listing project root…");
    expect(humanizeToolInProgress("grep_search", { pattern: "TODO" })).toBe(
      "Searching code for TODO…",
    );
    expect(humanizeToolInProgress("shell_exec", { command: "ls -la" })).toBe("Running ls -la…");
    expect(humanizeToolInProgress("workflow_run", { workflow_name: "demo" })).toBe("Running demo…");
  });
  test("done", () => {
    expect(humanizeToolDone("file_read", { path: "/x/y.md" }, "line1\nline2")).toBe(
      "Read y.md (2 lines)",
    );
    expect(humanizeToolDone("glob_search", { pattern: "*.ts" }, "(no matches)")).toBe(
      "No matches for *.ts",
    );
    expect(humanizeToolDone("shell_exec", { command: "ls" }, "ok")).toBe("Ran ls → ok");
    expect(humanizeToolDone("python_exec", {}, "")).toBe("Ran Python");
  });
});

describe("workflow-activity", () => {
  test("isWorkflowTool", () => {
    expect(isWorkflowTool("workflow_run")).toBe(true);
    expect(isWorkflowTool("file_read")).toBe(false);
  });
  test("payload parse + error detect", () => {
    const p = parseWorkflowPayload('{"execution_id":"e1","status":"running"}', null);
    expect(p?.execution_id).toBe("e1");
    expect(isWorkflowError(p)).toBe(false);
    expect(isWorkflowError({ error: "workflow_error" })).toBe(true);
    expect(parseWorkflowPayload("plain text", null)).toBeNull();
  });
  test("in progress labels", () => {
    expect(formatWorkflowInProgress("workflow_wait", { workflow_name: "w", execution_id: "e" })).toBe(
      "Waiting on w (e)…",
    );
    expect(formatWorkflowInProgress("workflow_upload", { path: "/a/b.json" })).toBe(
      "Uploading b.json…",
    );
  });
  test("done labels", () => {
    expect(
      formatWorkflowDone("workflow_run", { workflow_name: "w" }, '{"execution_id":"e9","status":"running"}', null),
    ).toBe("Started w → e9 (running)");
    expect(formatWorkflowDone("workflow_list", {}, '{"count":3,"work":[1,2,3]}', null)).toBe(
      "3 workflows listed",
    );
    expect(
      formatWorkflowDone("workflow_status", {}, '{"error":"workflow_error","message":"nope"}', null),
    ).toBe("workflow failed: nope");
  });
});

describe("formatToolActivityEntry", () => {
  test("ok result", () => {
    const line = formatToolActivityEntry({
      type: "tool_result",
      message: "2 lines here",
      data: { name: "file_read", arguments: { path: "/x/a.md" } },
    });
    expect(line?.status).toBe("ok");
    expect(line?.text).toBe("✓ ◫ Read a.md (2 lines here)");
  });
  test("denied result gets warn + blocked flag", () => {
    const line = formatToolActivityEntry({
      type: "tool_result",
      message: "[tool_contract:permission_denied fs]",
      data: { name: "file_write", arguments: {} },
    });
    expect(line?.status).toBe("warn");
    expect(line?.blocked).toBe(true);
    expect(line?.text).toContain("⛔");
  });
  test("todo result summarizes checklist", () => {
    const line = formatToolActivityEntry({
      type: "tool_result",
      message: JSON.stringify([{ content: "x", status: "done" }]),
      data: { name: "TodoWrite", arguments: {} },
    });
    expect(line?.text).toContain("Todos 1/1 done");
  });
  test("tool_blocked label strips ellipsis", () => {
    const line = formatToolActivityEntry(
      { type: "tool_blocked", data: { name: "shell_exec", arguments: { command: "rm -rf /" } } },
      undefined,
    );
    expect(line?.text).toContain("blocked");
    expect(line?.text).not.toContain("… blocked");
  });
});

describe("collapse + turn summary", () => {
  test("repetitive reads collapse", () => {
    const lines: ActivityLine[] = [
      { status: "ok", text: "✓ ◫ Read a", name: "file_read" },
      { status: "ok", text: "✓ ◫ Read b", name: "file_read" },
      { status: "ok", text: "✓ ◫ Read c", name: "file_read" },
      { status: "ok", text: "✓ ☰ Found 2 for x", name: "grep_search" },
    ];
    const out = collapseToolActivity(lines);
    expect(out.map((l) => l.text)).toEqual(["☰ 1 code search", "◫ 3 files read"]);
  });
  test("errors break batching and survive", () => {
    const lines: ActivityLine[] = [
      { status: "ok", text: "✓ ◫ Read a", name: "file_read" },
      { status: "error", text: "✗ blowup", name: "shell_exec" },
      { status: "ok", text: "✓ ◫ Read b", name: "file_read" },
    ];
    const out = collapseToolActivity(lines);
    expect(out).toHaveLength(3);
    expect(out[1].text).toBe("blowup");
  });
  test("summarizeToolTurn", () => {
    const lines: ActivityLine[] = [
      { status: "ok", text: "✓ ◫ Read a", name: "file_read" },
      { status: "ok", text: "✓ ◫ Read b", name: "file_read" },
      { status: "ok", text: "✓ $ Ran ls", name: "shell_exec" },
    ];
    expect(summarizeToolTurn(lines)).toBe("$ 1 command run · ◫ 2 files read");
    expect(summarizeToolTurn([])).toBe("");
  });
});
