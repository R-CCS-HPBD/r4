import { describe, expect, test } from "bun:test";
import { testRender } from "@opentui/solid";
import { handleApprovalRequest } from "../src/bridge/approval-bridge";
import { actions, workspace } from "../src/stores";
import { ToolApprovalOverlay } from "../src/components/tool-approval-overlay";

const DIFF = [
  "--- a/f.py",
  "+++ b/f.py",
  "@@ -1 +1 @@",
  "-old_line",
  "+new_line",
].join("\n");

describe("approval diff parity (opencode EditBody)", () => {
  test("request diff flows into the pending approval state", () => {
    actions.closeToolApproval();
    handleApprovalRequest({
      id: 41,
      tool: "file_write",
      description: "Write file: f.py",
      diff: DIFF,
      path: "f.py",
    });
    expect(workspace.toolApproval?.diff).toBe(DIFF);
    expect(workspace.toolApproval?.path).toBe("f.py");
    actions.closeToolApproval();
  });

  test("empty/missing diff degrades to the classic blind card", () => {
    actions.closeToolApproval();
    handleApprovalRequest({ id: 42, tool: "shell_exec", description: "run ls" });
    expect(workspace.toolApproval?.diff).toBeUndefined();
    actions.closeToolApproval();
  });

  test("card renders the diff lines", async () => {
    actions.openToolApproval({
      id: 43,
      tool: "file_edit",
      description: "Edit f.py",
      sessionApprovalAllowed: true,
      diff: DIFF,
      path: "f.py",
    });
    const setup = await testRender(() => <ToolApprovalOverlay />, {
      width: 80,
      height: 30,
    });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("Tool approval required");
    // Side-by-side diff strips sign prefixes into two columns.
    expect(frame).toContain("old_line");
    expect(frame).toContain("new_line");
    setup.renderer.destroy();
    actions.closeToolApproval();
  });
});
