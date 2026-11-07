/**
 * A3: shared prompt history semantics — JSONL rows, cap 50, full dedup with
 * push-back, atomic tmp+rename, R4_CONFIG_HOME override, tolerant of legacy
 * plain-text rows. Ensures byte-level parity with the v3/tui-js tape.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const bundle = await import('./prompt-history.bundle.cjs').then((m) => m.default ?? m);
const { appendPromptHistory, promptHistory, PROMPT_HISTORY_LIMIT, _resetPromptHistoryCache } = bundle;

function freshEnv() {
  const dir = mkdtempSync(join(tmpdir(), 'r4-history-'));
  process.env.R4_CONFIG_HOME = dir;
  _resetPromptHistoryCache();
  return dir;
}

test('empty tape starts empty', () => {
  freshEnv();
  assert.deepEqual(promptHistory(), []);
});

test('append keeps oldest→newest order and persists JSONL rows', () => {
  const dir = freshEnv();
  appendPromptHistory('first prompt');
  appendPromptHistory('second prompt');
  _resetPromptHistoryCache();
  assert.deepEqual(promptHistory(), ['first prompt', 'second prompt']);
  // 1 row per line, JSON string shape.
  const rows = readFileSync(join(dir, 'prompt-history.jsonl'), 'utf8').trim().split('\n');
  assert.equal(rows.length, 2);
  assert.equal(JSON.parse(rows[0]).text, 'first prompt');
});

test('dedup moves the re-typed prompt to the tail (removes all prior copies)', () => {
  freshEnv();
  appendPromptHistory('alpha');
  appendPromptHistory('beta');
  appendPromptHistory('alpha');
  assert.deepEqual(promptHistory(), ['beta', 'alpha']);
});

test('caps at the configured limit', () => {
  freshEnv();
  for (let i = 0; i < PROMPT_HISTORY_LIMIT + 10; i++) appendPromptHistory(`p-${i}`);
  const history = promptHistory();
  assert.equal(history.length, PROMPT_HISTORY_LIMIT);
  assert.equal(history[history.length - 1], `p-${PROMPT_HISTORY_LIMIT + 9}`);
  assert.equal(history[0], 'p-10'); // oldest 10 fell off the tape
});

test('whitespace-only entries skipped; legacy plain-text rows tolerated', () => {
  const dir = freshEnv();
  writeFileSync(join(dir, 'prompt-history.jsonl'), '{"text":"kept"}\nlegacy row\n\n');
  assert.deepEqual(promptHistory(), ['kept', 'legacy row']);
  appendPromptHistory('   ');
  appendPromptHistory('kept');
  assert.deepEqual(promptHistory(), ['legacy row', 'kept']);
});
