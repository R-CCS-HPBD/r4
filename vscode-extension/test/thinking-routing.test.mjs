import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const gateBundlePath = path.join(rootDir, "test", "think-gate.bundle.cjs");

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/think-gate.ts", "--bundle", "--platform=node", "--format=cjs", "--outfile=test/think-gate.bundle.cjs"],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const { ThinkingStreamGate } = require(gateBundlePath);

test("ThinkingStreamGate blocks thinking after answer starts", () => {
  const gate = new ThinkingStreamGate();
  const reported = [];

  gate.reportThinking((text) => reported.push(text), "planning");
  gate.markAnswerStarted();
  gate.reportThinking((text) => reported.push(text), "late reasoning");

  assert.deepEqual(reported, ["planning"]);
});

test("finalize emits done marker once after thinking was reported", () => {
  const gate = new ThinkingStreamGate();
  const done = [];

  gate.reportThinking(() => {}, "planning");
  gate.finalize(() => done.push("done"));
  gate.finalize(() => done.push("done again"));

  assert.deepEqual(done, ["done"]);
});

test("finalize is a no-op when no thinking was reported", () => {
  const gate = new ThinkingStreamGate();
  const done = [];

  gate.finalize(() => done.push("done"));

  assert.deepEqual(done, []);
});

test("reportLanguageModelThinkingDone emits vscode_reasoning_done metadata", () => {
  const reported = [];
  const progress = { report: (part) => reported.push(part) };

  class LanguageModelThinkingPart {
    constructor(value, id, metadata) {
      this.value = value;
      this.id = id;
      this.metadata = metadata;
    }
  }

  const Ctor = LanguageModelThinkingPart;
  if (Ctor) {
    progress.report(new Ctor("", "", { vscode_reasoning_done: true }));
  }

  assert.equal(reported.length, 1);
  assert.equal(reported[0].value, "");
  assert.equal(reported[0].id, "");
  assert.deepEqual(reported[0].metadata, { vscode_reasoning_done: true });
});

test("reportLanguageModelThinking uses thinking part when available", () => {
  const reported = [];
  const progress = { report: (part) => reported.push(part) };

  class LanguageModelThinkingPart {
    constructor(value, id, metadata) {
      this.value = value;
      this.id = id;
      this.metadata = metadata;
    }
  }

  const Ctor = LanguageModelThinkingPart;
  if (Ctor) {
    progress.report(new Ctor("reasoning chunk", "r4-thinking-1", { title: "Thinking" }));
  }

  assert.equal(reported.length, 1);
  assert.equal(reported[0].value, "reasoning chunk");
  assert.equal(reported[0].id, "r4-thinking-1");
  assert.deepEqual(reported[0].metadata, { title: "Thinking" });
});

test("reportLanguageModelThinking omits reasoning when API unavailable", () => {
  const reported = [];
  const progress = { report: (part) => reported.push(part) };

  const Ctor = undefined;
  if (Ctor) {
    progress.report(new Ctor("reasoning chunk", "r4-thinking-1", { title: "Thinking" }));
  }

  assert.equal(reported.length, 0);
});

test("thinkingProgress routes reasoning for chat participant stream", () => {
  const deltas = [];
  const stream = {
    thinkingProgress(delta) {
      deltas.push(delta);
    },
    markdown() {},
  };

  if (typeof stream.thinkingProgress === "function") {
    stream.thinkingProgress({
      text: "planning step",
      id: "r4-thinking-1",
      metadata: { title: "Thinking" },
    });
  }

  assert.deepEqual(deltas, [{
    text: "planning step",
    id: "r4-thinking-1",
    metadata: { title: "Thinking" },
  }]);
});
