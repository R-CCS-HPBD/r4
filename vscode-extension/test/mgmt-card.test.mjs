/**
 * M5: host-side card query map — every card kind resolves to a distinct,
 * correctly-named bridge method; arg-required kinds reject missing args.
 */
import test from 'node:test';
import assert from 'node:assert/strict';

const { MGMT_CARD_QUERY_MAP, MGMT_KINDS, buildMgmtCardParams } =
  await import('./mgmt-card.bundle.cjs').then((m) => m.default ?? m);

test('every documented card kind has a bridge method', () => {
  const expected = [
    'wf-list',
    'wf-show',
    'wf-run',
    'wf-results',
    'loop-list',
    'loop-status',
    'session-view',
    'knowledge-status',
    'knowledge-detail',
    'knowledge-graph',
  ];
  for (const kind of expected) {
    assert.ok(MGMT_KINDS.has(kind), `missing kind: ${kind}`);
    assert.ok(MGMT_CARD_QUERY_MAP[kind]?.method, `missing method for ${kind}`);
  }
  // request-op is host-emitted, not queryable.
  assert.equal(MGMT_CARD_QUERY_MAP['request-op'], undefined);
});

test('bridge methods mirror the TUI contracts exactly', () => {
  const expectations = {
    'wf-list': 'workflow.list',
    'wf-show': 'workflow_describe',
    'wf-run': 'workflow.detail',
    'wf-results': 'workflow.results',
    'loop-list': 'research_loop.list',
    'loop-status': 'research_loop.status',
    'session-view': 'research_session.view',
    'knowledge-status': 'research_knowledge.status',
    'knowledge-detail': 'get_knowledge',
    'knowledge-graph': 'knowledge_graph_content',
  };
  for (const [kind, method] of Object.entries(expectations)) {
    assert.equal(MGMT_CARD_QUERY_MAP[kind].method, method, `${kind} → method`);
  }
});

test('params build: args land in the right slots', () => {
  assert.deepEqual(buildMgmtCardParams('wf-list'), {});
  assert.deepEqual(buildMgmtCardParams('wf-show', 'kb-build'), { workflow_name: 'kb-build' });
  assert.deepEqual(buildMgmtCardParams('wf-run', 'exec-1'), { execution_id: 'exec-1' });
  assert.deepEqual(buildMgmtCardParams('wf-results', 'exec-1'), { execution_id: 'exec-1' });
  assert.deepEqual(buildMgmtCardParams('loop-status', 'loop-9'), { loop_id: 'loop-9' });
  assert.deepEqual(buildMgmtCardParams('knowledge-graph', 'g-1'), { graph_id: 'g-1' });
  assert.deepEqual(buildMgmtCardParams('loop-list'), {});
  assert.deepEqual(buildMgmtCardParams('session-view'), {});
  assert.deepEqual(buildMgmtCardParams('knowledge-status'), {});
  assert.deepEqual(buildMgmtCardParams('knowledge-detail'), {});
});

test('missing required arg rejects', () => {
  assert.throws(() => buildMgmtCardParams('wf-run'), /needs an argument/);
  assert.throws(() => buildMgmtCardParams('wf-show'), /needs an argument/);
  assert.throws(() => buildMgmtCardParams('loop-status'), /needs an argument/);
  assert.throws(() => buildMgmtCardParams('knowledge-graph'), /needs an argument/);
});

test('unknown kind rejects', () => {
  assert.throws(() => buildMgmtCardParams('request-op'), /unknown management kind/);
  assert.throws(() => buildMgmtCardParams('bogus'), /unknown management kind/);
});
