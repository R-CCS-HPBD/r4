/**
 * Stabilization 2026-10-21 (render lane, panes-overlays#4) — /codeview's
 * inline diff is sized from the PANE, not the terminal.
 *
 * CodeViewRoute picked side-by-side from the terminal width (≥110) and
 * rendered ParallelDiffBlock without a width, so each cell was
 * (terminal−1)/2 ≈ 74 cols inside a ~46-col pane: the header claimed
 * "(side-by-side)" while the NEW column (added/changed lines) was entirely
 * off-screen. The route now measures the pane and passes that width; a
 * pane narrower than the side-by-side threshold gets the unified diff.
 */

import { afterAll, beforeAll, beforeEach, describe, expect, it } from "bun:test";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { testRender } from "@opentui/solid";
import { Shell } from "../src/layout/shell";
import { resetWorkspace, actions, setWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

let repo = "";
const prevWs = process.env.R4_TUI_WORKSPACE;
// Hermetic git (2026-10-21): run from a pre-commit hook, the inherited
// GIT_DIR / GIT_INDEX_FILE / GIT_WORK_TREE pointed `git init` at the SHARED
// repository and set core.bare=true for every checkout. The writes below get
// an explicit env with every GIT_* variable removed (Bun children inherit
// the original environ, so deleting from process.env is not enough).
const hermeticEnv = Object.fromEntries(
  Object.entries(process.env).filter(([key]) => !key.startsWith("GIT_")),
);

function git(...args: string[]): void {
  const res = spawnSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], {
    cwd: repo,
    env: hermeticEnv,
  });
  if (res.status !== 0) throw new Error(String(res.stderr));
}

beforeAll(() => {
  repo = mkdtempSync(join(tmpdir(), "r4-codeview-"));
  git("init", "-q");
  writeFileSync(join(repo, "a.txt"), "line1\nline2\n");
  git("add", "a.txt");
  git("commit", "-q", "-m", "base");
  writeFileSync(join(repo, "a.txt"), "line1 changed\nline2\nline3\n");
  process.env.R4_TUI_WORKSPACE = repo;
});

afterAll(() => {
  if (prevWs === undefined) delete process.env.R4_TUI_WORKSPACE;
  else process.env.R4_TUI_WORKSPACE = prevWs;
  rmSync(repo, { recursive: true, force: true });
});

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("/codeview diff preview width", () => {
  // 110 cols → ~30-col pane: unified diff, the changed line crops with "…".
  for (const [width, changed] of [[110, "+ line1 chan"], [150, "+ line1 changed"]] as const) {
    it(`shows the NEW side of a modified file at ${width} cols`, async () => {
      setWorkspace("changeSets", [{ path: "a.txt", status: "M" }] as never);
      setWorkspace("changeCursor", 0);
      setWorkspace("paneView", "codeview" as never);
      const setup = await testRender(() => <Shell />, { width, height: 50 });
      for (let i = 0; i < 4; i++) await setup.renderOnce();
      const frame = setup.captureCharFrame();
      setup.renderer.destroy();
      expect(frame).toContain("git diff HEAD — a.txt");
      expect(frame).not.toContain("(side-by-side)");
      expect(frame).toContain(changed);
      expect(frame).toContain("+ line3");
    });
  }

  it("a pane dragged wide enough for side-by-side shows BOTH columns", async () => {
    setWorkspace("changeSets", [{ path: "a.txt", status: "M" }] as never);
    setWorkspace("changeCursor", 0);
    setWorkspace("paneView", "codeview" as never);
    setWorkspace("paneWidthOverride", 150 as never);
    const setup = await testRender(() => <Shell />, { width: 260, height: 50 });
    for (let i = 0; i < 4; i++) await setup.renderOnce();
    const frame = setup.captureCharFrame();
    setup.renderer.destroy();
    expect(frame).toContain("(side-by-side)");
    const row = frame.split("\n").find((l) => l.includes("- line1")) ?? "";
    expect(row).toContain("+ line1 changed");
    expect(frame).toContain("+ line3");
  });
});
