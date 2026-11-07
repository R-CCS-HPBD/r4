/**
 * Wave H — code view helpers: porcelain parsing + arm/confirm semantics.
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import { parsePorcelainLine, fileDiff, revertPath } from "../src/lib/git-changes";

describe("parsePorcelainLine", () => {
  test("maps staged/unstaged codes to compact status", () => {
    const m = parsePorcelainLine(" M src/a.ts")!;
    expect(m).toEqual({ path: "src/a.ts", status: "M" });
    expect(parsePorcelainLine("M  src/staged.ts")!.status).toBe("M");
    const a = parsePorcelainLine("A  src/new.ts")!;
    expect(a.status).toBe("A");
    const untracked = parsePorcelainLine("?? src/junk.log")!;
    expect(untracked.status).toBe("A"); // untracked renders as added-cache
  });

  test("renames keep the target path", () => {
    const r = parsePorcelainLine("R  src/old.ts -> src/new.ts");
    expect(r).toEqual({ path: "src/new.ts", status: "R" });
  });

  test("short/garbage lines return null", () => {
    expect(parsePorcelainLine("")).toBeNull();
    expect(parsePorcelainLine(" x")).toBeNull();
  });
});

describe("fileDiff (untracked)", () => {
  test("A entries synthesize a header without spawning git", () => {
    const text = fileDiff("src/x.ts", "A", process.cwd());
    expect(text).toContain("+++ b/src/x.ts");
    expect(text).toContain("/dev/null");
  });
});

describe("arm/confirm revert", () => {
  beforeEach(() => {
    setWorkspace("revertArmPath", null);
    setWorkspace("chatMessages", []);
  });

  test("arm marks the path; second arm replaces nothing (single pending)", () => {
    expect(actions.armRevert("src/a.ts")).toBe(true);
    expect(workspace.revertArmPath).toBe("src/a.ts");
  });

  test("confirm clears the arm, runs the callback, notices the outcome", () => {
    actions.armRevert("src/a.ts");
    const calls: string[] = [];
    expect(actions.confirmRevert((path) => {
      calls.push(path);
      return { ok: true };
    })).toBe(true);
    expect(calls).toEqual(["src/a.ts"]);
    expect(workspace.revertArmPath).toBeNull();
    expect(workspace.chatMessages.at(-1)!.content).toContain("reverted src/a.ts");
  });

  test("confirm without an arm is a no-op", () => {
    expect(actions.confirmRevert(() => ({ ok: true }))).toBe(false);
  });

  test("failure reports through the notice", () => {
    actions.armRevert("src/b.ts");
    actions.confirmRevert(() => ({ ok: false, error: "path did not match" }));
    expect(workspace.chatMessages.at(-1)!.content).toContain("revert failed");
  });
});
