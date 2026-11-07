import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "edit-session-core.bundle.cjs");
const esbuild = path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild");
execFileSync(
  esbuild,
  ["src/edit-session-core.ts", "--bundle", "--platform=node", "--format=cjs", `--outfile=${bundlePath}`],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const { applyAnchoredEdits, computeEditStats, EditSessionStore } = require(bundlePath);

const stageInput = (overrides = {}) => ({
  sessionId: "s1",
  filePath: "/ws/src/a.ts",
  relPath: "src/a.ts",
  originalText: "line1\nline2\nline3\n",
  modifiedText: "line1\nline2 changed\nline3\nline4\n",
  ...overrides,
});

test("computeEditStats counts added and removed lines", () => {
  assert.deepEqual(computeEditStats("a\nb\nc\n", "a\nx\nc\n"), { added: 1, removed: 1 });
  assert.deepEqual(computeEditStats("a\nb\nc\n", "a\nb\nc\nd\n"), { added: 1, removed: 0 });
  assert.deepEqual(computeEditStats("a\nb\nc\n", "a\nc\n"), { added: 0, removed: 1 });
  assert.deepEqual(computeEditStats(undefined, "new\nfile\n"), { added: 2, removed: 0 });
  assert.deepEqual(computeEditStats("gone\n", ""), { added: 0, removed: 1 });
  assert.deepEqual(computeEditStats("same\n", "same\n"), { added: 0, removed: 0 });
});

test("computeEditStats handles disjoint blocks (worst case counts)", () => {
  const oldText = "one\ntwo\nthree\n";
  const newText = "alpha\nbeta\ngamma\n";
  assert.deepEqual(computeEditStats(oldText, newText), { added: 3, removed: 3 });
});

test("computeEditStats keeps common anchors cheap in large files", () => {
  const oldLines = Array.from({ length: 400 }, (_, i) => `line ${i}`);
  const newLines = [...oldLines];
  newLines.splice(200, 1, "edited line");
  newLines.splice(10, 0, "inserted line");
  const stats = computeEditStats(oldLines.join("\n"), newLines.join("\n"));
  assert.deepEqual(stats, { added: 2, removed: 1 });
});

test("computeEditStats handles duplicate lines correctly", () => {
  // Hunt–Szymanski descending positions: both x-pairs must match.
  assert.deepEqual(computeEditStats("x\na\nx\nb", "c\nx\nd\nx"), { added: 2, removed: 2 });
  assert.deepEqual(computeEditStats("}\n}\n", "}\n}\n}\n"), { added: 1, removed: 0 });
  assert.deepEqual(computeEditStats("same\nsame\nsame", "same\nsame\nsame"), { added: 0, removed: 0 });
  assert.deepEqual(computeEditStats("a\n\nb\n\n", "a\n\n\nb\n\n"), { added: 1, removed: 0 });
});

test("applyAnchoredEdits replaces a unique anchor", () => {
  const out = applyAnchoredEdits("a\nb\nc\n", [{ oldString: "b", newString: "B" }]);
  assert.equal(out, "a\nB\nc\n");
});

test("applyAnchoredEdits rejects missing anchors", () => {
  assert.throws(
    () => applyAnchoredEdits("a\n", [{ oldString: "zzz", newString: "q" }]),
    /anchor not found/,
  );
});

test("applyAnchoredEdits rejects ambiguous anchors", () => {
  assert.throws(
    () => applyAnchoredEdits("x\nx\n", [{ oldString: "x", newString: "y" }]),
    /ambiguous/,
  );
});

test("applyAnchoredEdits empty anchor only valid for file creation", () => {
  assert.equal(applyAnchoredEdits("", [{ oldString: "", newString: "fresh\n" }]), "fresh\n");
  assert.throws(
    () => applyAnchoredEdits("existing", [{ oldString: "", newString: "x" }]),
    /only valid when creating/,
  );
});

test("applyAnchoredEdits chains sequential replacements", () => {
  const out = applyAnchoredEdits("a\nb\nc\n", [
    { oldString: "a", newString: "A" },
    { oldString: "c", newString: "C" },
  ]);
  assert.equal(out, "A\nb\nC\n");
});

test("store stages edits with stats and emits events", () => {
  const store = new EditSessionStore();
  const events = [];
  store.onDidChange((event) => events.push(event));
  const edit = store.stage(stageInput());
  assert.equal(edit.status, "pending");
  assert.deepEqual(edit.stats, { added: 2, removed: 1 });
  assert.ok(edit.id.startsWith("edit-"));
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "staged");
  assert.equal(events[0].edit.id, edit.id);
});

test("store rejects no-op edits and missing paths", () => {
  const store = new EditSessionStore();
  assert.throws(() => store.stage(stageInput({ modifiedText: "line1\nline2\nline3\n" })), /no changes/);
  assert.throws(() => store.stage(stageInput({ filePath: "" })), /filePath is required/);
});

test("re-staging the same file supersedes the earlier pending edit", () => {
  const store = new EditSessionStore();
  const events = [];
  store.onDidChange((event) => events.push(event));
  const first = store.stage(stageInput());
  const second = store.stage(stageInput({ modifiedText: "line1\nline2 v2\nline3\n" }));
  assert.equal(store.get(first.id), undefined);
  assert.equal(store.get(second.id)?.id, second.id);
  assert.equal(store.getForFile("/ws/src/a.ts")?.id, second.id);
  // staged, resolved (supersede), staged
  assert.deepEqual(events.map((event) => event.kind), ["staged", "resolved", "staged"]);
  assert.equal(events[1].edit.status, "rejected");
});

test("baseTextFor prefers staged text over disk text", () => {
  const store = new EditSessionStore();
  const edit = store.stage(stageInput());
  assert.equal(store.baseTextFor("/ws/src/a.ts", "disk\n"), edit.modifiedText);
  assert.equal(store.baseTextFor("/ws/other.ts", "disk\n"), "disk\n");
});

test("resolve transitions edit out of pending and emits", () => {
  const store = new EditSessionStore();
  const events = [];
  store.onDidChange((event) => events.push(event));
  const edit = store.stage(stageInput());
  const resolved = store.resolve(edit.id, "accepted");
  assert.equal(resolved.status, "accepted");
  assert.equal(store.get(edit.id), undefined);
  assert.equal(events.at(-1).kind, "resolved");
  assert.throws(() => store.resolve(edit.id, "accepted"), /Unknown pending edit/);
});

test("clearSession discards all pending edits for one session only", () => {
  const store = new EditSessionStore();
  store.stage(stageInput());
  store.stage(stageInput({ filePath: "/ws/src/b.ts", relPath: "src/b.ts" }));
  store.stage(stageInput({ sessionId: "other", filePath: "/ws/src/c.ts", relPath: "src/c.ts" }));
  const events = [];
  store.onDidChange((event) => events.push(event));
  const cleared = store.clearSession("s1");
  assert.equal(cleared.length, 2);
  assert.ok(cleared.every((edit) => edit.status === "rejected"));
  assert.equal(store.listPending().length, 1);
  assert.equal(store.listPending()[0].sessionId, "other");
  assert.equal(events.length, 1);
  assert.equal(events[0].kind, "cleared");
});

test("listPending filters by session and returns copies", () => {
  const store = new EditSessionStore();
  const edit = store.stage(stageInput());
  const listed = store.listPending("s1");
  assert.equal(listed.length, 1);
  listed[0].status = "accepted";
  assert.equal(store.get(edit.id).status, "pending");
});
