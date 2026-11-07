/**
 * M5: management slash-verb resolution (webview → host mapping contracts).
 * Loads the esbuild-bundled webview lib (same bundle pattern as the other
 * extension tests) so TS sources are testable under plain node.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const { resolveMgmtVerb, MGMT_VERB_PREFIX, filterSlashSuggestions, WEBVIEW_SLASH_SUGGESTIONS } =
  await import('./mgmt-slash-parser.bundle.cjs').then((m) => m.default ?? m);

test('workflow aliases resolve to wf-list', () => {
  assert.deepEqual(resolveMgmtVerb('/workflow list'), { kind: 'wf-list' });
  assert.deepEqual(resolveMgmtVerb('/workflow ls'), { kind: 'wf-list' });
  assert.deepEqual(resolveMgmtVerb('/workflow'), { kind: 'wf-list' });
});

test('workflow show/status/results carry their arg', () => {
  assert.deepEqual(resolveMgmtVerb('/workflow show kb-build'), {
    kind: 'wf-show',
    arg: 'kb-build',
  });
  assert.deepEqual(resolveMgmtVerb('/workflow status exec-123'), {
    kind: 'wf-run',
    arg: 'exec-123',
  });
  assert.deepEqual(resolveMgmtVerb('/workflow results exec-123'), {
    kind: 'wf-results',
    arg: 'exec-123',
  });
});

test('workflow mutations stay on the approval path (null)', () => {
  assert.equal(resolveMgmtVerb('/workflow run kb-build'), null);
  assert.equal(resolveMgmtVerb('/workflow cancel exec-1'), null);
  assert.equal(resolveMgmtVerb('/workflow approve exec-1 gather'), null);
});

test('loop verbs', () => {
  assert.deepEqual(resolveMgmtVerb('/loop list'), { kind: 'loop-list' });
  assert.deepEqual(resolveMgmtVerb('/loop status loop-9'), {
    kind: 'loop-status',
    arg: 'loop-9',
  });
  assert.equal(resolveMgmtVerb('/loop stop loop-9'), null);
});

test('session / knowledge / graph verbs', () => {
  assert.deepEqual(resolveMgmtVerb('/session view'), { kind: 'session-view' });
  assert.deepEqual(resolveMgmtVerb('/knowledge'), { kind: 'knowledge-status' });
  assert.deepEqual(resolveMgmtVerb('/knowledge detail'), { kind: 'knowledge-detail' });
  assert.deepEqual(resolveMgmtVerb('/graph'), { kind: 'knowledge-status' });
  assert.deepEqual(resolveMgmtVerb('/graph graph-007'), {
    kind: 'knowledge-graph',
    arg: 'graph-007',
  });
});

test('non-verb text is untouched', () => {
  assert.equal(resolveMgmtVerb('workflow list (without slash)'), null);
  assert.equal(resolveMgmtVerb('/help'), null);
});

test('echo prefix matcher only fires on management verbs', () => {
  for (const good of ['/workflow list', '/loop list', '/session view', '/knowledge', '/graph g']) {
    assert.ok(MGMT_VERB_PREFIX.test(good), good);
  }
  for (const bad of ['/help', '/undo', 'regular chat']) {
    assert.ok(!MGMT_VERB_PREFIX.test(bad), bad);
  }
});

// ── A2: webview slash autocomplete catalog ──────────────────────────────

test('filter returns the full catalog on a bare slash', () => {
  assert.equal(filterSlashSuggestions('/').length, WEBVIEW_SLASH_SUGGESTIONS.length);
});

test('filter narrows by fragment prefix', () => {
  const matches = filterSlashSuggestions('/wo').map((s) => s.name);
  assert.deepEqual(matches, ['workflow']);
  assert.deepEqual(filterSlashSuggestions('/comp').map((s) => s.name), ['compact']);
});

test('filter closes once a space follows the verb', () => {
  assert.deepEqual(filterSlashSuggestions('/workflow '), []);
  assert.deepEqual(filterSlashSuggestions('not a slash'), []);
});

test('catalog marks which verbs run locally vs summon cards', () => {
  const local = filterSlashSuggestions('/').filter((s) => s.runs === 'local').map((s) => s.name).sort();
  assert.deepEqual(local, ['compact', 'undo']);
});
