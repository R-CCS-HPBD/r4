import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "store-tool-selection.bundle.cjs");

// Bundle ONLY the reducer-exposed surface (store context is React-heavy;
// esbuild bundles it fine even though these tests call only pure reducers).
execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  [
    "webview-ui/src/state/store.tsx",
    "--bundle",
    "--platform=node",
    "--format=cjs",
    "--loader:.tsx=tsx",
    `--outfile=${bundlePath}`,
  ],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const {
  toolSelectionLine,
  nextToolSelectionEntries,
  assistantCompletionEntries,
} = require(bundlePath);

function entry(partial) {
  return { id: partial.id ?? 1, kind: partial.kind ?? 'msg', text: partial.text ?? '', ...partial };
}

test("toolSelectionLine renders the passthrough honesty line", () => {
  const line = toolSelectionLine({ available_tools: 65, candidate_tools: 65, strategy: 'passthrough' });
  assert.equal(line, "⚙ all 65 tools offered (no selection)");
});

test("toolSelectionLine appends the skills suffix and strategy counts", () => {
  const passthrough = toolSelectionLine({
    available_tools: 72,
    candidate_tools: 72,
    strategy: 'passthrough',
    selected_skills: ['kg-ontology'],
  });
  assert.equal(passthrough, "⚙ all 72 tools offered (no selection) · 1 skill");

  const filtered = toolSelectionLine({
    available_tools: 142,
    candidate_tools: 78,
    strategy: 'policy_filter',
    selected_skills: ['a', 'b'],
  });
  assert.equal(filtered, "⚙ 78 of 142 tools offered (policy_filter) · 2 skills");
});

test("toolSelection records collapse into one updatable roll-up entry", () => {
  const prior = [entry({ id: 1, kind: 'msg', role: 'You', text: 'question' })];
  const first = nextToolSelectionEntries(prior, { available_tools: 65, candidate_tools: 65, strategy: 'passthrough' });
  assert.equal(first.length, 2, "user message + one roll-up line");
  assert.equal(first[1].tag, 'toolSelection');
  assert.equal(first[1].text, "⚙ all 65 tools offered (no selection)");

  // Second model call (count drifted 65 -> 72) replaces, never stacks.
  const second = nextToolSelectionEntries(first, { available_tools: 72, candidate_tools: 72, strategy: 'passthrough' });
  const rollups = second.filter((e) => e.tag === 'toolSelection');
  assert.equal(rollups.length, 1, "exactly one roll-up line after an update");
  assert.equal(rollups[0].text, "⚙ all 72 tools offered (no selection)");
});

test("assistantComplete with empty text leaves a visible trace (dead-window guard)", () => {
  const closed = [entry({ id: 1, kind: 'msg', role: 'You', text: 'hello' })];
  const next = assistantCompletionEntries(closed, "", false);
  assert.equal(next.length, 2, "empty completion must not be a silent gap");
  assert.equal(next[1].kind, 'event');
  assert.match(next[1].text, /no content/);
});

test("dead-window guard stays quiet on tool-only turns (H.5)", () => {
  const closed = [entry({ id: 1, kind: 'msg', role: 'You', text: 'run the tool-only thing' })];
  const next = assistantCompletionEntries(closed, "", false, true);
  assert.equal(next.length, 1, "tool-only turn: cards are evidence — no 'no content' line");
});

test("dead-window guard still fires when the turn had no tools and no text", () => {
  const closed = [entry({ id: 1, kind: 'msg', role: 'You', text: 'hi' })];
  const next = assistantCompletionEntries(closed, "", false, false);
  assert.equal(next.length, 2);
  assert.match(next[1].text, /no content/);
});

test("assistantComplete with text appends the agent message", () => {
  const closed = [entry({ id: 1, kind: 'msg', role: 'You', text: 'hello' })];
  const next = assistantCompletionEntries(closed, "answer", false);
  assert.equal(next.length, 2);
  assert.equal(next[1].role, 'R4 Agent');
  assert.equal(next[1].text, 'answer');
});

test("streamed draft is replaced by the authoritative final text", () => {
  const closed = [
    entry({ id: 1, kind: 'msg', role: 'You', text: 'hello' }),
    entry({ id: 2, kind: 'msg', role: 'R4 Agent', text: 'partial draf' }),
  ];
  const next = assistantCompletionEntries(closed, "final answer", true);
  assert.equal(next.length, 2, "draft replaced in place, not duplicated");
  assert.equal(next[1].text, 'final answer');
});
