/**
 * Knowledge-node visual helpers — shared by the Knowledge panel and the
 * Sigma constellation (extracted from KnowledgePanel.tsx in the Wave B swap).
 */

import type { KnowledgeNode } from '@/types/protocol';

const TYPE_COLORS: Record<string, string> = {
  hypothesis: '#b088f9',
  experiment: '#f2c94c',
  evidence: '#6fcf97',
  finding: '#56ccf2',
  paper: '#6fcf97',
  dataset: '#f2994a',
  code: '#56ccf2',
  notebook: '#bb6bd9',
  figure: '#eb5757',
  goal: '#9b51e0',
  task: '#f2c94c',
  knowledge: '#56ccf2',
  // Canonical-spine node classes (document/chunk/file/entity/edge) so the map
  // actually communicates the graph shape, not just the semantic kind.
  document: '#f2994a',
  chunk: '#9ec1ff',
  file: '#f7dc6f',
  entity: '#6fcf97',
  edge: '#96a5b4',
};

export function typeColor(t: string | undefined): string {
  return TYPE_COLORS[(t ?? 'knowledge').toLowerCase()] ?? '#56ccf2';
}

/** Color kind: semantic_type (durable store) wins over the DB category. */
export function semanticKind(node: KnowledgeNode): string {
  const meta = node.metadata as Record<string, unknown> | undefined;
  const semantic = typeof meta?.semantic_type === 'string' ? meta.semantic_type : '';
  return (semantic || node.type || 'knowledge').toLowerCase();
}

/** Provenance hues for selected (satellite) knowledge graphs — shared by the
 *  constellation and the panel legend so colors line up anywhere in the UI. */
const SATELLITE_COLORS = ['#3fd6c9', '#f2994a', '#c678dd', '#f7dc6f', '#6fa8dc'];

export function satelliteColor(index: number): string {
  return SATELLITE_COLORS[index % SATELLITE_COLORS.length];
}
