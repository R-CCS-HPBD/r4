import { test, expect } from "bun:test";
import { buildApprovalPreview } from "../src/lib/approval-preview";

test("shell_exec renders $-style command rows from the Run shell command prefix", () => {
  const rows = buildApprovalPreview({
    tool: "shell_exec",
    description: "Run shell command: make build && make test",
  });
  expect(rows[0].label).toBe("$");
  expect(rows[0].text).toBe("make build && make test");
});

test("file_write keeps the path row out of generic echo", () => {
  const rows = buildApprovalPreview({
    tool: "file_write",
    description: "Write file: src/agent.py",
  });
  expect(rows[0].label).toBe("path");
  expect(rows[0].text).toBe("src/agent.py");
});

test("workflow_upload shows function + params", () => {
  const rows = buildApprovalPreview({
    tool: "workflow_upload",
    description:
      "Upload workflow bundle: /tmp/example-video-bundle: pipeline, step: 4",
  });
  expect(rows[0].label).toBe("function");
  expect(rows.length).toBeGreaterThanOrEqual(2);
});

test("egress patterns render as pattern rows", () => {
  const rows = buildApprovalPreview({
    tool: "egress",
    description: "api.example.com, data.feed.net",
  });
  expect(rows[0].label).toBe("pattern");
});

test("fallback renders one bounded detail row", () => {
  const rows = buildApprovalPreview({ tool: "custom_tool", description: "x".repeat(80) });
  expect(rows.length).toBe(1);
  expect(rows[0].label).toBe("detail");
});
