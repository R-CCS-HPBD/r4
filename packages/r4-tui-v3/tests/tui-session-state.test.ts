import { afterEach, describe, expect, test } from "bun:test";
import { mkdirSync, rmSync, writeFileSync, readFileSync, existsSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";

/**
 * tui-session-state pins (A1 2026-10-05): atomic debounced writes, index-
 * based active, absent/corrupt → null (fresh start), filtering of dead rows.
 */

const home = join(tmpdir(), `r4-tabs-state-${process.pid}`);

// Restore, never delete: the test preload's scratch home must survive
// this file (r2 #34/#46).
const savedHome = process.env.R4_CONFIG_HOME;

describe("tui-session-state", () => {
  afterEach(() => {
    if (savedHome === undefined) delete process.env.R4_CONFIG_HOME;
    else process.env.R4_CONFIG_HOME = savedHome;
    rmSync(home, { recursive: true, force: true });
  });

  function envHome() {
    mkdirSync(home, { recursive: true });
    process.env.R4_CONFIG_HOME = home;
  }

  test("round trip: write debounced, read clamps and normalizes", async () => {
    envHome();
    const { readTabsState, writeTabsState, flushTabsState } = await import(
      "../src/lib/tui-session-state"
    );
    writeTabsState(
      {
        tabs: [
          { title: "main", sessionId: "s-111" },
          { title: "bench", sessionId: "s-222" },
        ],
        activeIndex: 7, // out of range — must clamp
      },
      10,
    );
    await flushTabsState();
    const state = readTabsState();
    expect(state).toEqual({
      tabs: [
        { title: "main", sessionId: "s-111" },
        { title: "bench", sessionId: "s-222" },
      ],
      activeIndex: 1,
    });
  });

  test("absent file reads null (fresh start)", async () => {
    envHome();
    const { readTabsState } = await import("../src/lib/tui-session-state");
    expect(readTabsState()).toBeNull();
  });

  test("corrupt file reads null, never throws", async () => {
    envHome();
    writeFileSync(join(home, "tui-session-state.json"), "{ broken json", "utf-8");
    const { readTabsState } = await import("../src/lib/tui-session-state");
    expect(readTabsState()).toBeNull();
  });

  test("rows without a session id never restore", async () => {
    envHome();
    writeFileSync(
      join(home, "tui-session-state.json"),
      JSON.stringify({
        tabs: [{ title: "x", sessionId: "" }, { title: "" }],
        activeIndex: 0,
      }),
      "utf-8",
    );
    const { readTabsState } = await import("../src/lib/tui-session-state");
    expect(readTabsState()).toBeNull();
  });

  test("write is atomic — tmp path never lingers", async () => {
    envHome();
    const { writeTabsState, flushTabsState } = await import("../src/lib/tui-session-state");
    writeTabsState({ tabs: [{ title: "t", sessionId: "s-1" }], activeIndex: 0 }, 10);
    await flushTabsState();
    expect(existsSync(join(home, "tui-session-state.json.tmp"))).toBe(false);
    const parsed = JSON.parse(readFileSync(join(home, "tui-session-state.json"), "utf-8"));
    expect(parsed.tabs[0].sessionId).toBe("s-1");
  });
});
