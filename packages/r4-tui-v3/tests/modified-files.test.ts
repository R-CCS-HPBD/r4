import { describe, expect, test } from "bun:test";
import {
  hasRenderableDiff,
  modifiedFilesFromToolCalls,
} from "../src/lib/modified-files";
import type { ActivityLine } from "../src/lib/tool-activity";

function line(partial: Partial<ActivityLine>): ActivityLine {
  return { status: "ok", text: "tool", ...partial };
}

const EDIT_RESULT = [
  "Edited src/a.py (replaced 1 occurrence)",
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1,2 +1,2 @@",
  "-old_line",
  "+new_line",
  " ctx",
].join("\n");

const WRITE_NEW_RESULT = [
  "Written 120 bytes to src/new.py",
  "--- a/src/new.py",
  "+++ b/src/new.py",
  "@@ -0,0 +1,3 @@",
  "+alpha",
  "+beta",
  "+gamma",
].join("\n");

describe("modifiedFilesFromToolCalls", () => {
  test("file_edit with embedded diff yields path + diffText", () => {
    const files = modifiedFilesFromToolCalls([
      line({ name: "file_edit", args: { path: "src/a.py" }, resultText: EDIT_RESULT }),
    ]);
    expect(files).toHaveLength(1);
    expect(files[0]!.path).toBe("src/a.py");
    expect(files[0]!.diffText).toContain("--- a/src/a.py");
    expect(files[0]!.diffText).toContain("+new_line");
    expect(files[0]!.summary).toContain("Edited src/a.py");
  });

  test("new-file write drops the all-add diff → styled code view path", () => {
    const files = modifiedFilesFromToolCalls([
      line({
        name: "file_write",
        args: { path: "src/new.py", content: "alpha\nbeta\ngamma\n" },
        resultText: WRITE_NEW_RESULT,
      }),
    ]);
    expect(files).toHaveLength(1);
    expect(files[0]!.diffText).toBeNull();
    expect(files[0]!.content).toBe("alpha\nbeta\ngamma\n");
    expect(hasRenderableDiff(files[0]!)).toBe(false);
  });

  test("same path edited twice keeps the LAST call (turn view)", () => {
    const calls = [
      line({ name: "file_edit", args: { path: "src/a.py" }, resultText: EDIT_RESULT }),
      line({
        name: "file_edit",
        args: { path: "src/a.py" },
        resultText: EDIT_RESULT.replace("new_line", "second_edit"),
      }),
    ];
    const files = modifiedFilesFromToolCalls(calls);
    expect(files).toHaveLength(1);
    expect(files[0]!.diffText).toContain("+second_edit");
  });

  test("error results and non-write tools are excluded", () => {
    const files = modifiedFilesFromToolCalls([
      line({
        name: "file_edit",
        args: { path: "src/missing.py" },
        resultText: "Error: old_text not found in src/missing.py",
      }),
      line({ name: "file_read", args: { path: "src/a.py" }, resultText: "contents" }),
      line({ name: "shell_exec", args: { cmd: "ls" }, resultText: "ok" }),
    ]);
    expect(files).toHaveLength(0);
  });

  test("file_write captures args.content for the styled code view", () => {
    const files = modifiedFilesFromToolCalls([
      line({
        name: "file_write",
        args: { path: "src/new.py", content: "alpha\nbeta\ngamma\n" },
        resultText: WRITE_NEW_RESULT,
      }),
    ]);
    expect(files).toHaveLength(1);
    expect(files[0]!.content).toBe("alpha\nbeta\ngamma\n");
    // New-file write: the all-add diff is dropped in favor of the code view.
    expect(hasRenderableDiff(files[0]!)).toBe(false);
  });

  test("file_edit without content arg leaves content undefined", () => {
    const files = modifiedFilesFromToolCalls([
      line({ name: "file_edit", args: { path: "src/a.py" }, resultText: EDIT_RESULT }),
    ]);
    expect(files[0]!.content).toBeUndefined();
  });

  test("write tool without path arg yields nothing", () => {
    const files = modifiedFilesFromToolCalls([
      line({ name: "file_write", args: {}, resultText: WRITE_NEW_RESULT }),
    ]);
    expect(files).toHaveLength(0);
  });

  test("undefined/empty toolCalls → empty list", () => {
    expect(modifiedFilesFromToolCalls(undefined)).toEqual([]);
    expect(modifiedFilesFromToolCalls([])).toEqual([]);
  });
});

describe("hasRenderableDiff", () => {
  test("null diff → false", () => {
    expect(
      hasRenderableDiff({ path: "x", tool: "file_edit", diffText: null, summary: "ok" }),
    ).toBe(false);
  });
  test("header-pair diff → true even without deletions", () => {
    expect(
      hasRenderableDiff({
        path: "x",
        tool: "file_write",
        diffText: "--- a/x\n+++ b/x\n+one",
        summary: "",
      }),
    ).toBe(true);
  });
});
