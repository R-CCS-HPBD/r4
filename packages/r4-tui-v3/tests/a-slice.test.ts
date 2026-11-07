import { test, expect } from "bun:test";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join as joinPath } from "node:path";

import {
  extractMentionQuery,
  rankMentions,
  scanWorkspaceFiles,
} from "../src/lib/file-mentions";
import { formatShellForAgent, formatShellPreview } from "../src/lib/local-shell";

test("extractMentionQuery picks a trailing @token", () => {
  expect(extractMentionQuery("@src/uti")?.query).toBe("src/uti");
  expect(extractMentionQuery("run please @notes/md")?.query).toBe("notes/md");
  expect(extractMentionQuery("no mention here")).toBeNull();
  expect(extractMentionQuery("foo@@bar")).toBeNull();
  expect(extractMentionQuery("@")).toBeNull();
});

test("scanWorkspaceFiles skips node_modules and binary files", () => {
  const root = mkdtempSync(joinPath(tmpdir(), "mentions-"));
  mkdirSync(joinPath(root, "src/deep"), { recursive: true });
  mkdirSync(joinPath(root, "node_modules/pkg"), { recursive: true });
  writeFileSync(joinPath(root, "src/deep/util.ts"), "export {};\n");
  writeFileSync(joinPath(root, "src/index.ts"), "export {};\n");
  writeFileSync(joinPath(root, "docs-readme.md"), "# doc\n");
  writeFileSync(joinPath(root, "node_modules/pkg/ignored.ts"), "x\n");
  writeFileSync(joinPath(root, "image.png"), "PNG");

  const files = scanWorkspaceFiles(root);
  expect(files).toContain("src/index.ts");
  expect(files).toContain("src/deep/util.ts");
  expect(files).toContain("docs-readme.md");
  expect(files).not.toContain("node_modules/pkg/ignored.ts");
  expect(files).not.toContain("image.png");
});

test("rankMentions ranks tight subsequences first, caps at limit", () => {
  const files = [
    "src/components/chat.tsx",
    "src/ui/MarkdownBlock.tsx",
    "packages/r4-tui-v3/src/lib/file-mentions.ts",
    "README.md",
    "packages/r4/tests/companion/chat-nothing.ts",
  ];
  const top = rankMentions(files, "chat", 3);
  expect(top.length).toBeLessThanOrEqual(3);
  expect(top[0].path).toContain("chat");
  // docs named exactly 'chat' floats above deeper non-chat paths
  const mdTop = rankMentions(files, "markdown", 2);
  expect(mdTop[0].path).toBe("src/ui/MarkdownBlock.tsx");
});

test("shell preview keeps header + last lines, marks no-output", () => {
  const r1 = formatShellPreview({
    command: "ls",
    exitCode: 0,
    stdout: "a\nb\nc",
    stderr: "",
    durationMs: 5,
  });
  const lines = r1.split("\n");
  expect(lines[0]).toBe("$ ls  (exit 0, 5ms)");
  expect(lines.slice(-3)).toEqual(["a", "b", "c"]);

  const r2 = formatShellPreview({
    command: "true",
    exitCode: 0,
    stdout: "",
    stderr: "",
    durationMs: 3,
  });
  expect(r2).toContain("(no output)");

  const long = formatShellPreview(
    {
      command: "seq",
      exitCode: 0,
      stdout: Array.from({ length: 30 }, (_, i) => `${i}`).join("\n"),
      stderr: "",
      durationMs: 8,
    },
    4,
  );
  expect(long.split("\n").length).toBe(5); // header + last 4
  expect(long).not.toContain("\n5\n");
});

test("shell preview default is UNCAPPED — full stdout survives (2026-10-01)", () => {
  const preview = formatShellPreview({
    command: "seq",
    exitCode: 0,
    stdout: Array.from({ length: 60 }, (_, i) => `line-${i}`).join("\n"),
    stderr: "",
    durationMs: 3,
  });
  const lines = preview.split("\n");
  expect(lines.length).toBe(61); // header + all 60
  expect(preview).toContain("line-0");
  expect(preview).toContain("line-59");
});

test("formatShellForAgent frames full output for the model (2026-10-01)", () => {
  const out = Array.from({ length: 40 }, (_, i) => `row-${i}`).join("\n");
  const text = formatShellForAgent({
    command: "big-cmd --verbose",
    exitCode: 3,
    stdout: out,
    stderr: "warn: something",
    durationMs: 12,
  });
  expect(text).toContain("!big-cmd --verbose");
  expect(text).toContain("TUI's local shell");
  expect(text).toContain("Exit code: 3");
  expect(text).toContain("--- stdout ---");
  expect(text).toContain("row-0");
  expect(text).toContain("row-39"); // uncapped
  expect(text).toContain("--- stderr ---");
  expect(text).toContain("warn: something");
});

test("formatShellForAgent marks empty streams honestly", () => {
  const text = formatShellForAgent({
    command: "true",
    exitCode: 0,
    stdout: "",
    stderr: "",
    durationMs: 2,
  });
  expect(text).toContain("(empty)");
});
