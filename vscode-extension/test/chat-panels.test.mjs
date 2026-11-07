// Chat tabs (ADR-103 VP-3b) against a scripted vscode API and fake bridges.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { EventEmitter } from "node:events";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const outDir = mkdtempSync(path.join(os.tmpdir(), "r4-chat-panels-"));
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");
for (const [entry, out] of [
  ["src/bridge/chat-panels.ts", "panels.cjs"],
  ["src/bridge/tab-title.ts", "title.cjs"],
]) {
  execFileSync(esbuild, [entry, "--bundle", "--platform=node", "--format=cjs", "--external:vscode", `--outfile=${path.join(outDir, out)}`], {
    stdio: "inherit",
    cwd: rootDir,
  });
}

/** The vscode API surface the panels touch. */
const panels = [];
const notices = [];
const stub = {
  ViewColumn: { Active: -1 },
  Uri: { joinPath: (base, ...parts) => ({ path: [base.path, ...parts].join("/") }) },
  window: {
    createWebviewPanel: (_viewType, title) => {
      const events = new EventEmitter();
      const posted = [];
      const panel = {
        title,
        active: true,
        revealed: 0,
        disposed: false,
        posted,
        webview: {
          html: "",
          cspSource: "csp",
          asWebviewUri: (u) => ({ toString: () => `vscode-webview://${u.path}` }),
          postMessage: async (m) => posted.push(m),
          onDidReceiveMessage: (fn) => events.on("message", fn),
        },
        onDidChangeViewState: (fn) => events.on("view", fn),
        onDidDispose: (fn) => events.on("dispose", fn),
        reveal() {
          this.revealed += 1;
        },
        dispose() {
          this.disposed = true;
          events.emit("dispose");
        },
        send: (m) => events.emit("message", m),
        setActive(active) {
          this.active = active;
          events.emit("view");
        },
      };
      panels.push(panel);
      return panel;
    },
    showWarningMessage: async (m) => notices.push(m),
    showInformationMessage: async (m) => notices.push(m),
  },
  workspace: { workspaceFolders: [{ uri: { fsPath: os.tmpdir() } }] },
  commands: { executeCommand: async () => undefined },
};
const stubPath = path.join(outDir, "vscode-stub.cjs");
writeFileSync(stubPath, "module.exports = globalThis.__r4VscodeStub;");
globalThis.__r4VscodeStub = stub;
const require = createRequire(import.meta.url);
const Module = require("node:module");
const original = Module._resolveFilename;
Module._resolveFilename = function resolve(request, ...rest) {
  if (request === "vscode") return stubPath;
  return original.call(this, request, ...rest);
};
const { ChatPanels, MAX_CHAT_TABS } = require(path.join(outDir, "panels.cjs"));
const { tabTitle } = require(path.join(outDir, "title.cjs"));
Module._resolveFilename = original;
process.on("exit", () => rmSync(outDir, { recursive: true, force: true }));

/** A bridge that answers initialize-time calls; records shutdowns. */
function fakeBridgeFactory() {
  const made = [];
  const factory = (initialSessionId) => {
    const bridge = new EventEmitter();
    Object.assign(bridge, {
      initialSessionId,
      shutdowns: 0,
      initResult: { session: { id: initialSessionId || `s-${made.length + 1}`, name: initialSessionId ? "Resumed" : "Session 2026-11-01 10:00" } },
      turnInFlight: false,
      async ensureStarted() {},
      async reconnect() {},
      async request(method) {
        return method === "list_sessions" ? { sessions: [] } : {};
      },
      respondApproval() {},
      respondAskUser() {},
      cancelTurn() {},
      async shutdown() {
        this.shutdowns += 1;
      },
    });
    made.push(bridge);
    return bridge;
  };
  return { factory, made };
}

function setup() {
  panels.length = 0;
  notices.length = 0;
  const { factory, made } = fakeBridgeFactory();
  const tabs = new ChatPanels({ path: "/ext" }, factory, async () => [{ type: "inventory", endpoints: [], connected: true }]);
  return { tabs, made };
}

const nonceOf = (panel) => /data-nonce="([^"]+)"/.exec(panel.webview.html)[1];
const tick = () => new Promise((r) => setImmediate(r));

test("a tab has its own bridge, page and nonce; ready initializes it and sends the startup state", async () => {
  const { tabs, made } = setup();
  assert.equal(tabs.open(), true);
  const panel = panels[0];
  assert.match(panel.webview.html, /data-nonce=/);
  panel.send({ type: "ready", messageToken: nonceOf(panel) });
  await tick();
  await tick();
  assert.equal(made.length, 1);
  assert.ok(panel.posted.some((m) => m.type === "sessionSelected"));
  assert.ok(panel.posted.some((m) => m.type === "inventory"), "the model picker gets the inventory");
  assert.equal(panel.title, "R4 chat", "the create-time placeholder is not a name");
});

test("messages without the tab's nonce are dropped; non-chat ones point to the sidebar", async () => {
  const { tabs, made } = setup();
  tabs.open();
  const panel = panels[0];
  panel.send({ type: "ready", messageToken: "wrong" });
  await tick();
  assert.equal(panel.posted.length, 0);
  panel.send({ type: "refreshStudio", messageToken: nonceOf(panel) });
  await tick();
  assert.match(panel.posted.at(-1).message, /sidebar's R4 Agent view/);
  assert.equal(made.length, 1);
});

test("the title follows the session; ● while a turn runs, ! for a background approval", async () => {
  const { tabs } = setup();
  tabs.open();
  const panel = panels[0];
  panel.send({ type: "ready", messageToken: nonceOf(panel) });
  await tick();
  const host = [...tabs.tabs][0].host;
  const post = (m) => tabs.post([...tabs.tabs][0], m);
  post({ type: "sessionSelected", session: { id: host.currentSessionId, name: "Refactor the parser" } });
  assert.equal(panel.title, "Refactor the parser");
  post({ type: "sessionState", state: "running" });
  assert.equal(panel.title, "● Refactor the parser");
  post({ type: "sessionState", state: "idle" });
  assert.equal(panel.title, "Refactor the parser");
  panel.setActive(false);
  post({ type: "approvalRequest", id: 1 });
  assert.equal(panel.title, "! Refactor the parser");
  panel.setActive(true);
  assert.equal(panel.title, "Refactor the parser", "looking at the tab clears the mark");
});

test(`at most ${MAX_CHAT_TABS} tabs; a session already open is revealed, not duplicated`, async () => {
  const { tabs } = setup();
  for (let i = 0; i < MAX_CHAT_TABS; i += 1) assert.equal(tabs.open(), true, `tab ${i + 1} opens`);
  assert.equal(tabs.open(), false, "the ninth is refused");
  assert.match(notices.at(-1), /at most 8 chat tabs/);
  const first = [...tabs.tabs][0];
  first.panel.dispose();
  assert.equal(tabs.open("s-x"), true, "a slot is free again");
  const before = panels.length;
  assert.equal(tabs.open("s-x"), true);
  assert.equal(panels.length, before, "no second tab for the same session");
});

test("closing shuts the tab's bridge down; reopen resumes its session from the journal", async () => {
  const { tabs, made } = setup();
  tabs.open();
  const panel = panels[0];
  panel.send({ type: "ready", messageToken: nonceOf(panel) });
  await tick();
  const sessionId = [...tabs.tabs][0].host.currentSessionId;
  panel.dispose();
  assert.equal(made[0].shutdowns, 1);
  assert.equal(tabs.count, 0);
  assert.equal(tabs.reopen(), true);
  assert.equal(made[1].initialSessionId, sessionId, "the new bridge initializes into the closed session");
  assert.equal(tabs.reopen(), false, "nothing more to reopen");
});

test("/tab verbs: list, <n>, next/prev wrap, close the focused tab, reopen, usage", () => {
  const { tabs } = setup();
  assert.match(tabs.command("")[0].text, /No chat tabs open/);
  tabs.command("new");
  tabs.open("s-b");
  tabs.open("s-c");
  for (const p of panels) p.active = false;
  panels[1].active = true;
  const list = tabs.command("list").map((l) => l.text);
  assert.deepEqual(list, ["  1. R4 chat", "▸ 2. R4 chat", "  3. R4 chat"]);
  assert.deepEqual(tabs.command("3"), []);
  assert.equal(panels[2].revealed, 1);
  tabs.command("next");
  assert.equal(panels[2].revealed, 2, "next from tab 2 is tab 3");
  panels[1].active = false;
  panels[2].active = true;
  tabs.command("next");
  assert.equal(panels[0].revealed, 1, "next wraps to tab 1");
  tabs.command("prev");
  assert.equal(panels[1].revealed, 1);
  assert.match(tabs.command("9")[0].text, /No chat tab 9/);
  tabs.command("close");
  assert.equal(panels[2].disposed, true, "close takes the focused tab");
  tabs.command("close 1");
  assert.equal(panels[0].disposed, true);
  assert.match(tabs.command("close")[0].text, /No chat tab is focused/);
  assert.equal(tabs.count, 1);
  assert.match(tabs.command("sideways")[0].text, /^\/tab \[list/);
  const noticesBefore = notices.length;
  assert.deepEqual(tabs.command("reopen"), [], "a closed tab reopens");
  assert.equal(tabs.count, 2);
  tabs.command("reopen");
  assert.match(tabs.command("reopen")[0].text, /No closed chat tab/);
  assert.equal(notices.length, noticesBefore, "the card speaks, not a toast");
});

test("tab titles: placeholder, cut at 28, marks", () => {
  assert.equal(tabTitle("Session 2026-11-01 10:00", ""), "R4 chat");
  assert.equal(tabTitle("", "●"), "● R4 chat");
  assert.equal(tabTitle("A".repeat(40), "!"), `! ${"A".repeat(27)}…`);
});
