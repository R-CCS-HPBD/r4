/**
 * ADR-101 (local tool sandbox): the console's pure sandbox rules — the
 * snapshot mapping, the status chip, the session-start notice text, the
 * `/sandbox` texts, the launch preset (`r4 --sandbox`) and the web-access
 * dialog's sandbox-dependent note.
 *
 * Maintainer requirement: "default -> off as claude code but r4 tui needs to
 * notify users and how to make this on during a session at first".
 */

import { afterEach, describe, expect, it } from "bun:test";
import type { SandboxState } from "../src/bridge/rpc-types.gen";
import {
  SANDBOX_USAGE,
  mapSandbox,
  sandboxChip,
  sandboxDetailLines,
  sandboxRowText,
  sandboxSetText,
  sandboxStartText,
  sandboxStatusText,
  type SandboxView,
} from "../src/lib/sandbox";
import { LOCAL_NOTE, LOCAL_NOTE_SANDBOXED, localNote, noteRows } from "../src/lib/web-access";
import { mapBridgeStateToWorkspace } from "../src/bridge/map-state";
import {
  LAUNCH_SANDBOX_ENV,
  bridgeArgv,
  resetLaunchSandboxForTest,
  takeLaunchSandbox,
} from "../src/bridge/jsonl-client";
import { OFF_NOTICE, wireSandbox } from "./helpers/sandbox-wire";

function view(over: Partial<SandboxState> = {}): SandboxView {
  return mapSandbox(wireSandbox(over))!;
}

describe("snapshot mapping", () => {
  it("maps the bridge's SandboxState; an absent key leaves the tab alone", () => {
    const patch = mapBridgeStateToWorkspace({ session: { sandbox: wireSandbox() } }, undefined);
    expect(patch.sandbox?.mode).toBe("off");
    expect(patch.sandbox?.tmpDir).toBe("/tmp/r4-sandbox-1000/s-abc/tmp");
    expect(patch.sandbox?.writeRoots).toEqual(["/work/proj", "/tmp/r4-sandbox-1000/s-abc/tmp"]);
    expect(patch.sandbox?.notice).toBe(OFF_NOTICE);
    expect(mapBridgeStateToWorkspace({ session: {} }, undefined).sandbox).toBeUndefined();
  });

  it("junk is not a sandbox; an unknown mode reads as off", () => {
    expect(mapSandbox(null)).toBeNull();
    expect(mapSandbox("on")).toBeNull();
    expect(mapSandbox([])).toBeNull();
    expect(mapSandbox({ mode: "sideways" })?.mode).toBe("off");
  });
});

describe("status chip", () => {
  it("sbx:on ok · sbx:off warn · sbx:n/a error", () => {
    expect(sandboxChip(view({ mode: "on", notice: "" }), "local")).toEqual({ label: "sbx:on", state: "ok" });
    expect(sandboxChip(view(), "local")).toEqual({ label: "sbx:off", state: "warn" });
    expect(sandboxChip(view({ available: false, reason: "bwrap not found" }), "local")).toEqual({
      label: "sbx:n/a",
      state: "error",
    });
  });

  it("none for a frontend session, a remote state or an old bridge", () => {
    expect(sandboxChip(view(), "frontend")).toBeNull();
    expect(sandboxChip(view({ mode: "remote", notice: "" }), "local")).toBeNull();
    expect(sandboxChip(null, "local")).toBeNull();
    expect(sandboxChip(undefined, "local")).toBeNull();
  });
});

describe("the session-start notice", () => {
  it("frames the bridge's own words; nothing without a notice", () => {
    expect(sandboxStartText(view(), "local")).toBe(`⚠ ${OFF_NOTICE}`);
    expect(sandboxStartText(view({ mode: "on", notice: "" }), "local")).toBe("");
    expect(sandboxStartText(view(), "frontend")).toBe("");
    expect(sandboxStartText(null, "local")).toBe("");
  });

  it("an on session whose MCP servers did not follow names the backend", () => {
    const text = sandboxStartText(view({ mode: "on", notice: "", mcp_error: "daemon not restarted" }), "local");
    expect(text).toBe("⚠ Sandbox on (bwrap) · MCP: daemon not restarted");
    const off = sandboxStartText(view({ mcp_error: "daemon not restarted" }), "local");
    expect(off).toBe(`⚠ ${OFF_NOTICE} · MCP: daemon not restarted`);
    // review Q-5: no prefix protocol: a notice that starts with "MCP:" is just a notice
    expect(sandboxStartText(view({ mode: "on", notice: "MCP: x" }), "local")).toBe("⚠ MCP: x");
  });

  it("the detail lines are the bridge's own, the backend and /sandbox status", () => {
    const lines = sandboxDetailLines(view()).join("\n");
    for (const part of ["workspace", "per-session temp", "extra_write_paths", "credential", "removed", "R4_GATEWAY_TOKEN stay", "proxy", "backend: bwrap", "/sandbox status"]) {
      expect(lines).toContain(part);
    }
    expect(sandboxDetailLines(view({ detail: ["only this"] }))).toContain("  only this");
    expect(sandboxDetailLines(view({ available: false, reason: "bwrap not found" })).join("\n")).toContain(
      "backend: unavailable (bwrap not found)",
    );
  });

  it("after /sandbox on the row becomes the short ✓ line", () => {
    expect(sandboxRowText(view({ mode: "on", notice: "" }))).toMatch(/^✓ Sandbox on \(bwrap\)/);
    expect(sandboxRowText(view())).toBe(`⚠ ${OFF_NOTICE}`);
  });
});

describe("/sandbox texts", () => {
  it("status shows mode, source, backend, write roots, network and tmp dir", () => {
    const text = sandboxStatusText(view({ mode: "on", source: "slash", notice: "" }));
    expect(text).toContain("sandbox: on (source: slash)");
    expect(text).toContain("backend: bwrap");
    expect(text).toContain("writes: /work/proj, /tmp/r4-sandbox-1000/s-abc/tmp");
    expect(text).toContain("network: isolated (web: off)");
    expect(text).toContain("tmp dir: /tmp/r4-sandbox-1000/s-abc/tmp");
  });

  // tui-lane review 2026-10-30: a frontend session's remote_state() has no
  // backend; it read "backend: no backend" as if this session had none.
  it("a frontend session names the server's sandbox, not 'no backend'", () => {
    const remote = view({
      mode: "remote",
      source: "venue",
      backend: "",
      available: true,
      reason: "this session runs in the server's sandbox (ADR-046)",
      tmp_dir: "",
      write_roots: [],
      network: "remote sandbox proxy",
      notice: "",
    });
    const text = sandboxStatusText(remote);
    expect(text).toContain("sandbox: remote (source: venue)");
    expect(text).toContain("backend: server sandbox (ADR-046)");
    expect(text).toContain("network: remote sandbox proxy");
    expect(text).toContain("this session runs in the server's sandbox");
    expect(text).not.toContain("no backend");
    expect(text).not.toContain("writes:");
    expect(text).not.toContain("tmp dir:");
  });

  it("a set names the MCP servers restarted, the MCP error and what still runs unconfined", () => {
    const text = sandboxSetText(
      { sandbox: view({ mode: "on", notice: "" }), restartedMcp: ["a", "b"], stillRunning: ["lsp:pyright"], mcpError: "stale" },
      false,
    );
    expect(text).toContain("sandbox on (bwrap)");
    expect(text).toContain("MCP servers restarted under the sandbox: a, b");
    expect(text).toContain("⚠ MCP: stale");
    expect(text).toContain("still running without the sandbox: lsp:pyright; stop them if needed");
    const off = sandboxSetText({ sandbox: view(), restartedMcp: [], stillRunning: [], mcpError: "" }, true);
    expect(off).toBe("sandbox off — the agent's next commands run with your full permissions · default for new sessions: off");
  });

  it("the usage names every verb", () => {
    expect(SANDBOX_USAGE).toContain("status|on|off|default on|default off");
  });
});

describe("r4 --sandbox reaches the bridge as a launch argument", () => {
  afterEach(() => resetLaunchSandboxForTest());

  it("takes the flag out of the environment and keeps it for restarts", () => {
    resetLaunchSandboxForTest();
    const env: Record<string, string | undefined> = { [LAUNCH_SANDBOX_ENV]: "on" };
    expect(takeLaunchSandbox(env)).toBe("on");
    expect(env[LAUNCH_SANDBOX_ENV]).toBeUndefined();
    expect(takeLaunchSandbox(env)).toBe("on");
  });

  it("accepts the flag words the bridge accepts; anything else is no preset", () => {
    for (const [raw, want] of [["OFF", "off"], ["on", "on"], ["yes", null], ["0", null], ["maybe", null], ["", null]] as const) {
      resetLaunchSandboxForTest();
      expect(takeLaunchSandbox({ [LAUNCH_SANDBOX_ENV]: raw })).toBe(want);
    }
  });

  it("passes --sandbox after --web-access, and nothing without one", () => {
    expect(bridgeArgv("py", "/w", "public", "off")).toEqual([
      "py",
      "-m",
      "r4.companion.ui.bridge_server",
      "--fs-root",
      "/w",
      "--web-access",
      "public",
      "--sandbox",
      "off",
    ]);
    expect(bridgeArgv("py", "/w", null, "on").slice(-2)).toEqual(["--sandbox", "on"]);
    expect(bridgeArgv("py", "/w", null)).not.toContain("--sandbox");
  });
});

describe("the web-access dialog's local note follows the sandbox (ADR-101 D-6)", () => {
  it("off (or an old bridge): not filtered, and /sandbox filters them; on: the sandbox proxy", () => {
    expect(localNote(null)).toBe(LOCAL_NOTE);
    expect(localNote({ mode: "off" })).toContain("turn on /sandbox");
    expect(localNote({ mode: "on" })).toBe(LOCAL_NOTE_SANDBOXED);
    expect(LOCAL_NOTE_SANDBOXED).toContain("through its proxy");
  });

  it("the note splits over two rows at a word, so the /sandbox hint is not cut", () => {
    const [first, second] = noteRows(LOCAL_NOTE, 60);
    expect(first.length).toBeLessThanOrEqual(60);
    expect(`${first} ${second}`).toBe(LOCAL_NOTE);
    expect(`${first} ${second}`.indexOf("/sandbox")).toBeLessThan(120);
    expect(noteRows("short", 60)).toEqual(["short", ""]);
  });
});
