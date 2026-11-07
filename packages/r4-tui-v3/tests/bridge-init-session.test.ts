/**
 * Stabilization 2026-10-21 (w3-tui, item 4) — a tab's bridge initializes
 * into the tab's session.
 *
 * Every new tab bridge sent `initialize {}`, and bridge_server.initialize
 * creates (and saves) a fresh session when no session_id is given — so each
 * tab open, and each /sessions resume in a new tab, left an empty session
 * record behind before load_session switched to the wanted one. The bridge
 * now initializes with the tab's known session id; an id the bridge does not
 * know falls back to a fresh session (the resume then reports its outcome).
 *
 * Hermetic: R4_TUI_PYTHON points at a shim that runs a tiny JSONL server
 * logging every initialize's params.
 */

import { afterAll, afterEach, beforeAll, describe, expect, it } from "bun:test";
import { chmodSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CompanionBridgeClient } from "../src/bridge/jsonl-client";
import { actions, workspace } from "../src/stores";
import { bridgeForTab, registerBridgeAttacher, resetTabsRegistryForTests } from "../src/tabs/registry";

const SERVER = `
import { appendFileSync } from "node:fs";
const log = process.env.FAKE_INIT_LOG;
const known = new Set(["sess-known-1"]);
let buf = "";
process.stdin.on("data", (chunk) => {
  buf += chunk.toString();
  let nl;
  while ((nl = buf.indexOf("\\n")) >= 0) {
    const line = buf.slice(0, nl).trim();
    buf = buf.slice(nl + 1);
    if (!line) continue;
    const req = JSON.parse(line);
    const reply = (body) => process.stdout.write(JSON.stringify({ id: req.id, ...body }) + "\\n");
    if (req.method !== "initialize") { reply({ ok: true, result: {} }); continue; }
    appendFileSync(log, JSON.stringify(req.params) + "\\n");
    const sid = req.params.session_id;
    if (sid && !known.has(sid)) reply({ ok: false, error: { message: "session not found: " + sid, type: "ValueError" } });
    else reply({ ok: true, result: { bridge_protocol: 1, session: { id: sid || "sess-fresh" }, messages: [] } });
  }
});
`;

let root = "";
let initLog = "";
const prevPython = process.env.R4_TUI_PYTHON;
const prevLog = process.env.FAKE_INIT_LOG;
const clients: CompanionBridgeClient[] = [];

beforeAll(() => {
  root = mkdtempSync(join(tmpdir(), "r4-init-session-"));
  initLog = join(root, "init.log");
  writeFileSync(join(root, "server.mjs"), SERVER);
  const bun = process.execPath;
  const shim = join(root, "python");
  writeFileSync(
    shim,
    ["#!/usr/bin/env bash", `if [ "$1" = "-m" ]; then exec "${bun}" "${join(root, "server.mjs")}"; fi`, "exit 0", ""].join("\n"),
  );
  chmodSync(shim, 0o755);
  process.env.R4_TUI_PYTHON = shim;
  process.env.FAKE_INIT_LOG = initLog;
});

afterEach(async () => {
  for (const c of clients.splice(0)) await c.shutdown();
  writeFileSync(initLog, "");
  resetTabsRegistryForTests();
});

afterAll(() => {
  if (prevPython === undefined) delete process.env.R4_TUI_PYTHON;
  else process.env.R4_TUI_PYTHON = prevPython;
  if (prevLog === undefined) delete process.env.FAKE_INIT_LOG;
  else process.env.FAKE_INIT_LOG = prevLog;
  rmSync(root, { recursive: true, force: true });
});

const inits = () =>
  readFileSync(initLog, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l) as Record<string, unknown>);

describe("tab bridge initialize", () => {
  it("a tab bound to a session initializes into it — no empty session record", async () => {
    writeFileSync(initLog, "");
    registerBridgeAttacher(() => undefined);
    const tabId = actions.tabCreate({ sessionId: "sess-known-1", title: "resumed" })!;
    expect(workspace.chatTabs.find((t) => t.id === tabId)?.sessionId).toBe("sess-known-1");
    const bridge = bridgeForTab(tabId);
    clients.push(bridge);
    await bridge.ensureStarted();
    expect(inits()).toEqual([{ session_id: "sess-known-1" }]);
  });

  it("an id the bridge does not know falls back to one fresh session", async () => {
    writeFileSync(initLog, "");
    const stderr: string[] = [];
    const bridge = new CompanionBridgeClient({ initialSessionId: () => "sess-gone-9" });
    clients.push(bridge);
    bridge.on("stderr", (s: string) => stderr.push(s));
    await bridge.ensureStarted();
    expect(bridge.alive).toBe(true);
    expect(inits()).toEqual([{ session_id: "sess-gone-9" }, {}]);
    expect(stderr.join("")).toContain("sess-gon");
  });

  it("a tab with no session still initializes a new one", async () => {
    writeFileSync(initLog, "");
    const bridge = new CompanionBridgeClient({ initialSessionId: () => "" });
    clients.push(bridge);
    await bridge.ensureStarted();
    expect(inits()).toEqual([{}]);
  });
});
