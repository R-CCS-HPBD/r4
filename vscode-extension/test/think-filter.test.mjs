import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createRequire } from "node:module";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const bundlePath = path.join(rootDir, "test", "think-filter.bundle.cjs");

execFileSync(
  path.join(rootDir, "node_modules", "esbuild", "bin", "esbuild"),
  ["src/think-filter.ts", "--bundle", "--platform=node", "--format=cjs", "--outfile=test/think-filter.bundle.cjs"],
  { stdio: "inherit", cwd: rootDir },
);

const require = createRequire(import.meta.url);
const { ThinkTagFilter } = require(bundlePath);

test("strips balanced thinking tags from content", () => {
  const filter = new ThinkTagFilter();
  assert.equal(
    filter.push("hello <think>secret</think> world"),
    "hello  world",
  );
});

test("routes balanced thinking tags to callback", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));

  assert.equal(filter.push("hello <think>secret</think> world"), "hello  world");
  assert.deepEqual(thinking, ["secret"]);
});

test("drops leading orphan close tag and preceding reasoning", () => {
  const filter = new ThinkTagFilter();
  assert.equal(
    filter.push("planning text</think>Answer"),
    "Answer",
  );
});

test("routes leading orphan reasoning to callback", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));

  assert.equal(filter.push("planning text</think>Answer"), "Answer");
  assert.deepEqual(thinking, ["planning text"]);
});

test("drops content inside a complete thinking block", () => {
  const filter = new ThinkTagFilter();
  assert.equal(filter.push("<think>only reasoning"), "");
  assert.equal(filter.push("</think>visible"), "visible");
});

test("routes streamed thinking chunks to callback", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));

  assert.equal(filter.push("<think>only "), "");
  assert.equal(filter.push("reasoning</think>visible"), "visible");
  assert.deepEqual(thinking, ["only ", "reasoning"]);
});

test("flush returns trailing visible text outside thinking block", () => {
  const filter = new ThinkTagFilter();
  assert.equal(filter.push("visible tail"), "visible tail");
  assert.equal(filter.flush(), "");
});

test("ignores thinking tags inside inline code", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));

  assert.equal(
    filter.push("strip `</think>` tags from output"),
    "strip `</think>` tags from output",
  );
  assert.deepEqual(thinking, []);
});

test("ignores thinking tags inside fenced code blocks", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));

  assert.equal(filter.push("```\n<think>"), "");
  assert.equal(filter.push("not reasoning\n```\n"), "```\n<think>not reasoning\n```\n");
  assert.deepEqual(thinking, []);
});

test("streams fenced code across deltas without misrouting tags", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));

  assert.equal(filter.push("render "), "render ");
  assert.equal(filter.push("```\n"), "");
  assert.equal(filter.push("code\n"), "");
  assert.equal(filter.push("```\nanswer"), "```\ncode\n```\nanswer");
  assert.deepEqual(thinking, []);
});

test("beginInThinking routes leading reasoning-first stream to thinking", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));
  filter.beginInThinking();

  // Reasoning streamed over several deltas with no opening <think> (it was
  // injected into the prompt); only a closing </think> precedes the answer.
  assert.equal(filter.push("Now I know the "), "");
  assert.equal(filter.push("symptom is bad.\n"), "");
  assert.equal(filter.push("</think>The real answer."), "The real answer.");
  assert.deepEqual(thinking, ["Now I know the ", "symptom is bad.\n"]);
});

test("beginInThinking restores code inside reasoning instead of sentinels", () => {
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));
  filter.beginInThinking();

  assert.equal(filter.push("check `m.id` here</think>done"), "done");
  assert.deepEqual(thinking, ["check `m.id` here"]);
});

test("beginInThinking is a no-op once visible text has been emitted", () => {
  const filter = new ThinkTagFilter();
  assert.equal(filter.push("visible answer"), "visible answer");
  filter.beginInThinking();
  assert.equal(filter.push(" more"), " more");
});

test("beginInThinking recovers the answer when no closing </think> arrives", () => {
  // Reasoning-first model guess is wrong: the content channel carried the real
  // answer (reasoning off / edits emitted directly), and no </think> ever came.
  // The answer must not be swallowed into the thinking UI or dropped.
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));
  filter.beginInThinking();

  assert.equal(filter.push("Here is the "), "");
  assert.equal(filter.push("file edit:\n```py\nx=1\n```\n"), "");
  // Stream ends with no closing tag -> recover buffered content as visible.
  assert.equal(filter.flush(), "Here is the file edit:\n```py\nx=1\n```\n");
});

test("beginInThinking still routes reasoning to thinking when </think> arrives", () => {
  // Confirmed guess: the close tag disarms the safety net, so flush() does not
  // re-emit the genuine reasoning as the answer.
  const thinking = [];
  const filter = new ThinkTagFilter((text) => thinking.push(text));
  filter.beginInThinking();

  assert.equal(filter.push("reasoning here"), "");
  assert.equal(filter.push("</think>The answer."), "The answer.");
  assert.equal(filter.flush(), "");
  assert.deepEqual(thinking, ["reasoning here"]);
});
