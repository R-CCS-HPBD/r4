/**
 * TUI audit r2 (2026-10-17), hooks lane — #70: the /statusline command ran
 * with spawnSync on the render thread every 5 s. The async runner keeps the
 * event loop free, honours the 2 s budget and is single-flight across the
 * status bars.
 */
import { afterAll, beforeAll, describe, expect, it } from "bun:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { refreshStatusline, runStatuslineAsync, writeStatuslineCommand } from "../src/lib/statusline";

const ctx = { model: "qwen", project: "proj-1", session: "s1", permission: "ask" };
const savedHome = process.env["R4_CONFIG_HOME"];
let dir = "";

beforeAll(() => {
  dir = mkdtempSync(join(tmpdir(), "tui-r2-statusline-"));
  process.env["R4_CONFIG_HOME"] = dir;
});

afterAll(() => {
  if (savedHome === undefined) delete process.env["R4_CONFIG_HOME"];
  else process.env["R4_CONFIG_HOME"] = savedHome;
  rmSync(dir, { recursive: true, force: true });
});

describe("async statusline", () => {
  it("returns the first stdout line with the R4_* context", async () => {
    expect(await runStatuslineAsync('printf "%s/%s\\nsecond" "$R4_PROJECT" "$R4_MODEL"', ctx)).toBe("proj-1/qwen");
    expect(await runStatuslineAsync("exit 3", ctx)).toBe("");
    expect(await runStatuslineAsync("", ctx)).toBe("");
  });

  it("does not block the event loop while the command runs", async () => {
    let ticked = false;
    setTimeout(() => {
      ticked = true;
    }, 10);
    const run = runStatuslineAsync("sleep 0.3; echo done", ctx);
    await new Promise((r) => setTimeout(r, 50));
    expect(ticked).toBe(true);
    expect(await run).toBe("done");
  });

  it("gives up after the timeout", async () => {
    const started = Date.now();
    expect(await runStatuslineAsync("sleep 3; echo late", ctx, 200)).toBe("");
    expect(Date.now() - started).toBeLessThan(1500);
  });

  it("gives up after the timeout when the command closes stdout and keeps running", async () => {
    const started = Date.now();
    expect(await runStatuslineAsync("echo hi; exec 1>&-; sleep 3", ctx, 300)).toBe("");
    expect(Date.now() - started).toBeLessThan(1500);
  });

  it("concurrent refreshes share one run", async () => {
    const counter = join(dir, "runs");
    writeFileSync(counter, "");
    writeStatuslineCommand(`echo x >> ${counter}; sleep 0.2; echo shared`);
    const [a, b] = await Promise.all([refreshStatusline(ctx), refreshStatusline(ctx)]);
    expect(a).toBe("shared");
    expect(b).toBe("shared");
    expect(readFileSync(counter, "utf8").trim().split("\n")).toHaveLength(1);
  });
});
