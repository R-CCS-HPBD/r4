import { describe, expect, test, beforeEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { ModifiedFilesCard } from "../src/components/chat";
import { actions, setWorkspace, workspace, rowExpanded } from "../src/stores";
import { buildToolDetailRows } from "../src/lib/tool-activity";
import { DIFF_IN_CARD_MARKER, stripEmbeddedDiff } from "../src/lib/modified-files";
import type { ModifiedFile } from "../src/lib/modified-files";
import type { ActivityLine } from "../src/lib/tool-activity";

const DIFF = [
  "--- a/src/a.py",
  "+++ b/src/a.py",
  "@@ -1,2 +1,2 @@",
  "-old_line",
  "+new_line",
  " ctx",
].join("\n");

const EDIT_FILE: ModifiedFile = {
  path: "src/a.py",
  tool: "file_edit",
  diffText: DIFF,
  summary: "Edited src/a.py (replaced 1 occurrence)",
};

const EDIT_RESULT = `Edited src/a.py (replaced 1 occurrence)\n${DIFF}`;

function resetRows(keys: string[]) {
  for (const k of keys) setWorkspace("noticeExpanded", k, undefined as never);
}

beforeEach(() => {
  if (workspace.verboseTranscript) actions.toggleVerboseTranscript();
  resetRows(["modfiles-collapse:m1", "modfile-collapse:m1:src/a.py"]);
});

describe("stripEmbeddedDiff + detail-row dedup", () => {
  test("strips at the first diff marker, keeps the summary head", () => {
    expect(stripEmbeddedDiff(EDIT_RESULT)).toBe("Edited src/a.py (replaced 1 occurrence)");
    expect(stripEmbeddedDiff("plain output\nno diff")).toBeNull();
  });

  test("write-family detail rows show summary + card marker, never the raw diff", () => {
    const line: ActivityLine = {
      status: "ok",
      text: "file_edit",
      name: "file_edit",
      args: { path: "src/a.py" },
      resultText: EDIT_RESULT,
    };
    const texts = buildToolDetailRows(line).map((r) => r.text);
    expect(texts.some((t) => t.includes("--- a/src/a.py"))).toBe(false);
    expect(texts.some((t) => t.includes(DIFF_IN_CARD_MARKER))).toBe(true);
    expect(texts.some((t) => t.includes("Edited src/a.py"))).toBe(true);
  });

  test("non-write tools keep their full output body", () => {
    const line: ActivityLine = {
      status: "ok",
      text: "shell_exec",
      name: "shell_exec",
      args: { command: "ls" },
      resultText: "file-one\nfile-two",
    };
    const texts = buildToolDetailRows(line).map((r) => r.text);
    expect(texts.some((t) => t.includes("file-one"))).toBe(true);
    expect(texts.some((t) => t.includes(DIFF_IN_CARD_MARKER))).toBe(false);
  });
});

describe("rowExpanded semantics (verbose is a default, not a lock)", () => {
  test("verbose expands by default; explicit click COLLAPSES under verbose", () => {
    if (!workspace.verboseTranscript) actions.toggleVerboseTranscript();
    expect(rowExpanded("probe-key")).toBe(true);
    actions.toggleRowExpanded("probe-key");
    expect(rowExpanded("probe-key")).toBe(false);
    setWorkspace("noticeExpanded", "probe-key", undefined as never);
    actions.toggleVerboseTranscript(); // relax back
  });

  test("default-expanded rows (modfiles card) collapse on first toggle", () => {
    expect(rowExpanded("modfiles-collapse:m99", true)).toBe(true);
    actions.toggleRowExpanded("modfiles-collapse:m99", true);
    expect(rowExpanded("modfiles-collapse:m99", true)).toBe(false);
    actions.toggleRowExpanded("modfiles-collapse:m99", true);
    expect(rowExpanded("modfiles-collapse:m99", true)).toBe(true);
  });
});

describe("ModifiedFilesCard — collapse click is functional (dead-click regression)", () => {
  test("clicking the card header collapses and re-expands", async () => {
    const setup = await testRender(
      () => <ModifiedFilesCard msgId="m1" files={[EDIT_FILE]} width={80} />,
      { width: 80, height: 20 },
    );
    await setup.renderOnce();
    // Starts expanded: diff content visible.
    expect(setup.captureCharFrame()).toContain("old_line");
    // Click the header row (located from the frame — the card breathes one
    // row above itself since the visibility wave 2026-10-19).
    const headerRow = () =>
      setup
        .captureCharFrame()
        .split("\n")
        .findIndex((l) => l.includes("file modified"));
    await setup.mockMouse.click(3, headerRow());
    await setup.renderOnce();
    expect(setup.captureCharFrame()).not.toContain("old_line");
    expect(setup.captureCharFrame()).toContain("▸ click expand");
    // Click again — back to expanded.
    await setup.mockMouse.click(3, headerRow());
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("old_line");
    setup.renderer.destroy();
  });
});
