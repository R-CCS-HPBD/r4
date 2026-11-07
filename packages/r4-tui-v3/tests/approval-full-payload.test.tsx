/**
 * The approval card shows everything the call runs (stabilization sweep
 * 2026-10-21, w3-py#1; chat#1 fixed the shell rows). python_exec code keeps
 * every line and its indentation, a tool without a server describer lists
 * every argument on its own row, and a diff the colored block would cut (too
 * many lines, a line past its column) is shown as scrolled rows instead.
 */

import { afterEach, describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { actions } from "../src/stores";
import { ToolApprovalOverlay } from "../src/components/tool-approval-overlay";
import { buildApprovalPreview } from "../src/lib/approval-preview";
import { resetTermWidthForTest, syncChatWidth } from "../src/lib/term-width";

afterEach(() => {
  actions.closeToolApproval();
  resetTermWidthForTest();
});

type CardPayload = { tool: string; description: string; diff?: string; path?: string };

async function renderCard(payload: CardPayload, width = 100, height = 60) {
  syncChatWidth(width);
  actions.openToolApproval({ id: 91, sessionApprovalAllowed: true, ...payload });
  const setup = await testRender(() => <ToolApprovalOverlay />, { width, height });
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  setup.renderer.destroy();
  return frame;
}

const CODE = "import shutil\nfor d in dirs:\n    shutil.rmtree(d)\nprint('done')";

describe("approval card shows the whole payload", () => {
  test("python_exec rows keep every line and its indentation", () => {
    const rows = buildApprovalPreview({ tool: "python_exec", description: `Run Python code:\n${CODE}` });
    expect(rows.map((r) => r.text)).toEqual(["Run Python code:", ...CODE.split("\n")]);
    expect(rows[1]!.label).toBe("python");
  });

  test("a fence line above the code stays on the card", () => {
    const rows = buildApprovalPreview({
      tool: "python_exec",
      description: `[fence: tainted]\nRun Python code:\n${CODE}`,
    });
    expect(rows[0]!.text).toBe("[fence: tainted]");
    expect(rows.at(-2)!.text).toBe("    shutil.rmtree(d)");
  });

  test("the python card renders the indented line", async () => {
    const frame = await renderCard({ tool: "python_exec", description: `Run Python code:\n${CODE}` });
    const column = (text: string) => frame.split("\n").find((l) => l.includes(text))!.indexOf(text);
    expect(column("shutil.rmtree(d)")).toBe(column("for d in dirs:") + 4);
  });

  test("a tool without a describer lists every argument on its own row", () => {
    const rows = buildApprovalPreview({
      tool: "workflow_run",
      description: 'Call tool: workflow_run\nworkflow_name: wf\nparams: {"a": 1, "b": "x, y"}',
    });
    expect(rows.map((r) => r.text)).toEqual([
      "Call tool: workflow_run",
      "workflow_name: wf",
      'params: {"a": 1, "b": "x, y"}',
    ]);
  });

  test("a diff longer than the block shows every line in the scrolled rows", async () => {
    const body = Array.from({ length: 40 }, (_, i) => `+line_${i}`);
    const diff = ["--- a/big.py", "+++ b/big.py", "@@ -0,0 +1,40 @@", ...body].join("\n");
    const frame = await renderCard({ tool: "file_write", description: "Write file: big.py", diff, path: "big.py" });
    expect(frame).toContain("rows, 12 visible — scroll here");
    expect(frame).toContain("+line_0");
  });

  test("a diff line past the block's column is shown whole", async () => {
    const long = `+x = 1; ${"pad ".repeat(30)}import os; os.system('HIDDEN_TAIL')`;
    const diff = ["--- a/f.py", "+++ b/f.py", "@@ -1 +1 @@", "-x = 1", long].join("\n");
    const frame = await renderCard({ tool: "file_edit", description: "Edit file: f.py", diff, path: "f.py" });
    expect(frame.replace(/\s+/g, "")).toContain("os.system('HIDDEN_TAIL')");
  });
});
