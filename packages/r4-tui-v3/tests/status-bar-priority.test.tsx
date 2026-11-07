/**
 * Status bar width priority (stabilization 2026-10-21, resize-render#4):
 * at ≤120 cols the fixed-width CPU/RAM/host chip kept its full width while
 * the active model, mode, permission and the signed-in state were squeezed
 * to nothing. The user-critical segments now win; the host chip shortens
 * (hostname first), then drops.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { StatusBar } from "../src/layout/status-bar";
import { actions, resetWorkspace, setScopedField, setWorkspace } from "../src/stores";
import { mapSandbox } from "../src/lib/sandbox";
import { wireSandbox } from "./helpers/sandbox-wire";

const HOST = "wrkst-001.lab-node.example.org";

beforeEach(() => {
  resetWorkspace();
  actions.setCurrentModel("fake-md");
  actions.setInteractionModeState({ interactionMode: "chat", permissionMode: "auto_safe" });
  actions.setIdentityState({ projectId: "?", credential: "none" });
  setWorkspace("connection", { gateway: "connected", umrs: "degraded", hpc: "degraded" } as never);
  setWorkspace("unreadNotifications", 1);
  setWorkspace("resources", { cpu: { percent: 24.1 }, memory: { percent: 43.5 }, hostname: HOST });
});

afterEach(() => {
  resetWorkspace();
});

async function footer(width: number): Promise<string> {
  const setup = await testRender(() => <StatusBar />, { width, height: 1 });
  try {
    await setup.renderOnce();
    return setup.captureCharFrame().split("\n")[0] ?? "";
  } finally {
    setup.renderer.destroy();
  }
}

describe("status bar keeps model · mode and the identity at narrow widths", () => {
  for (const width of [80, 100, 120, 140]) {
    it(`${width} cols`, async () => {
      const line = await footer(width);
      expect(line).toContain("fake-md · mode:chat");
      expect(line).toContain("signed-out");
    });
  }

  it("ADR-104: one badge — autopilot names its trust source, no perm: badge", async () => {
    actions.setInteractionModeState({ interactionMode: "autopilot", permissionMode: "trust", trustSource: "server" });
    const line = await footer(140);
    expect(line).toContain("mode:autopilot(policy)");
    expect(line).not.toContain("perm:");
  });

  it("the host chip yields first: no hostname at 120, whole at 200", async () => {
    expect(await footer(120)).not.toContain("riken");
    expect(await footer(200)).toContain(`CPU 24.1% · RAM 43.5% · ${HOST}`);
  });
});

// w3-tui item 3 (2026-10-21): the engine's span shrink garbled the bar — the
// identity read "? · signed-   fake-a@fakesrc ·" at 140 cols, the left text
// was cut without an ellipsis ("agents 0/") and chips were clipped mid-label
// ("gw:", "umrs:degra"). Every segment is now whole or ellipsized, and a chip
// that does not fit is dropped whole.
describe("status bar segments are whole or ellipsized, never clipped", () => {
  const WEB_CHIP = /^web:(off|allow|public|\?)( srch:(up|down|none|…|\?))?$/;
  const CHIPS = new Set(["n:1", "gw:connected", "umrs:degraded", "hpc:degraded", "CPU 24.1% · RAM 43.5%", `CPU 24.1% · RAM 43.5% · ${HOST}`]);
  for (const width of [80, 100, 140, 200]) {
    it(`${width} cols`, async () => {
      actions.setCurrentModel("fake-a@fakesrc");
      const line = await footer(width);
      const mid = line.indexOf(" fake-a@");
      expect(mid).toBeGreaterThan(0);
      const left = line.slice(0, mid).trim();
      expect(left.startsWith("? · signed-out")).toBe(true);
      expect(left.endsWith("agents 0/0") || left.endsWith("…")).toBe(true);
      const right = line.slice(line.indexOf("mode:chat") + "mode:chat".length).trim();
      const chips = right.split(/\s{2,}/).filter(Boolean);
      expect(chips.length).toBeGreaterThan(0);
      // The web chip (2026-11-03) is reserved: present at every width.
      expect(chips.some((chip) => WEB_CHIP.test(chip))).toBe(true);
      for (const chip of chips) expect(CHIPS.has(chip) || WEB_CHIP.test(chip)).toBe(true);
    });
  }
});

// w3-tui (2026-10-21): bridges without host metrics send `cpu: null`. The
// summary's destructuring default only covered undefined, so the footer's
// render threw — and since the status bar re-renders inside the chat send's
// store write, the send aborted (the pty journeys' fixture never got a turn).
describe("status bar with a metrics-less resources snapshot", () => {
  it("cpu/memory null: renders, no CPU chip, and a turn start does not throw", async () => {
    setWorkspace("resources", { cpu: null, memory: null, hostname: "pty-fixture" } as never);
    const setup = await testRender(() => <StatusBar />, { width: 200, height: 1 });
    await setup.renderOnce();
    expect(() => setWorkspace("isTyping", true as never)).not.toThrow();
    await setup.renderOnce();
    const line = setup.captureCharFrame().split("\n")[0] ?? "";
    setup.renderer.destroy();
    expect(line).toContain("pty-fixture");
    expect(line).not.toContain("CPU");
  });
});

// ADR-101: the sandbox chip rides after the venue chip. It is one more chip
// in the greedy fit, so model · mode and the identity still win at 80
// columns; it is gone for a frontend session and for a bridge without the key.
describe("status bar sandbox chip", () => {
  const sandbox = (mode: "on" | "off", available = true) =>
    actions.setSandbox(mapSandbox(wireSandbox({ mode, available, notice: "" })));

  for (const width of [80, 100, 120, 140]) {
    it(`${width} cols: sbx:off never pushes model · mode off`, async () => {
      sandbox("off");
      const line = await footer(width);
      expect(line).toContain("fake-md · mode:chat");
      expect(line).toContain("signed-out");
      if (width >= 120) expect(line).toContain("sbx:off");
    });
  }

  // tui-lane review 2026-10-30: mid-turn the identity rides as a right chip
  // (2026-10-06 fix); sbx:off sat before it and pushed it off at 120 cols.
  it("mid-turn: the identity chip outranks sbx:off; both fit on a wide bar", async () => {
    actions.setIdentityState({ projectId: "proj-alpha", credential: "token" } as never);
    setWorkspace("unreadNotifications", 0);
    setScopedField("isTyping", true as never);
    sandbox("off");
    expect(await footer(120)).toContain("default/proj-alpha");
    const wide = await footer(140);
    expect(wide).toContain("default/proj-alpha");
    expect(wide).toContain("sbx:off");
    expect(wide.indexOf("proj-alpha")).toBeLessThan(wide.indexOf("sbx:off"));
  });

  it("sbx:on, sbx:n/a; none for a frontend session or an old bridge", async () => {
    sandbox("on");
    expect(await footer(140)).toContain("sbx:on");
    sandbox("off", false);
    expect(await footer(140)).toContain("sbx:n/a");
    sandbox("off");
    actions.setSessionVenue("frontend");
    expect(await footer(140)).not.toContain("sbx:");
    actions.setSessionVenue("local");
    actions.setSandbox(null);
    expect(await footer(140)).not.toContain("sbx:");
  });
});
