/**
 * M5 management slash-verb resolver — pure, port of the v3 canonical table
 * (packages/r4-tui-v3/src/commands/slash.ts). All three surfaces accept the
 * same verbs and produce the same card kind + argument bundle.
 *
 * Returns null when the verb should stay on the backend approval path
 * (mutations: run/cancel/approve, stop/start).
 */
import type { MgmtCardKind } from '@/types/protocol';

const WORKFLOW_CANONICAL: Record<string, MgmtCardKind | null> = {
  list: 'wf-list',
  ls: 'wf-list',
  '': 'wf-list',
  show: 'wf-show',
  describe: 'wf-show',
  info: 'wf-show',
  status: 'wf-run',
  state: 'wf-run',
  results: 'wf-results',
  output: 'wf-results',
};

function resolveWorkflowVerb(verb: string, tokens: string[]): { kind: MgmtCardKind; arg?: string } | null {
  if (!(verb in WORKFLOW_CANONICAL)) return null;
  const kind = WORKFLOW_CANONICAL[verb];
  if (!kind || kind === 'wf-list') return { kind: kind ?? 'wf-list' };
  const arg = tokens[1];
  return arg ? { kind, arg } : null;
}

function resolveLoopVerb(verb: string, tokens: string[]): { kind: MgmtCardKind; arg?: string } | null {
  if (verb === 'list' || verb === 'ls' || verb === '') return { kind: 'loop-list' };
  if (verb === 'status' || verb === 'show') {
    const arg = tokens[1];
    return arg ? { kind: 'loop-status', arg } : null;
  }
  return null;
}

/** Resolve `/workflow …`, `/loop …`, `/session view`, `/knowledge …`,
 * `/graph [id]` text into a management card query, or null when the text
 * should go through normal chat/approval paths. */
export function resolveMgmtVerb(body: string): { kind: MgmtCardKind; arg?: string } | null {
  const m = body.trim().match(/^\/(workflow|loop|session|knowledge|graph)\b\s*(.*)$/i);
  if (!m) return null;
  const group = m[1].toLowerCase();
  const tokens = (m[2] ?? '').trim().split(/\s+/).filter(Boolean);
  const verb = (tokens[0] ?? '').toLowerCase();

  if (group === 'workflow') return resolveWorkflowVerb(verb, tokens);
  if (group === 'loop') return resolveLoopVerb(verb, tokens);
  if (group === 'session') return verb === 'view' || !verb ? { kind: 'session-view' } : null;
  if (group === 'knowledge') {
    return verb === 'detail' ? { kind: 'knowledge-detail' } : { kind: 'knowledge-status' };
  }
  // group === 'graph'
  if (!verb) return { kind: 'knowledge-status' };
  return { kind: 'knowledge-graph', arg: verb };
}

/** Slash verb prefixes whose user echo is hidden once a card lands — the
 * card is the answer (TUI convention from the M1/M4 waves). */
export const MGMT_VERB_PREFIX = /^\/(workflow|loop|session|knowledge|graph)\b/i;

// ── A2: webview slash autocomplete catalog ──────────────────────────────

export interface SlashSuggestion {
  /** Verb name without the leading slash. */
  name: string;
  /** One-line hint shown in the popup. */
  hint: string;
  /** Local verbs run in the webview; mgmt verbs summon cards. */
  runs: 'local' | 'mgmt';
}

/** Static catalog of slash verbs this surface actually handles. */
export const WEBVIEW_SLASH_SUGGESTIONS: readonly SlashSuggestion[] = [
  { name: 'undo', hint: 'restore the last prompt into the composer', runs: 'local' },
  { name: 'compact', hint: 'summarize older conversation (/compact [N])', runs: 'local' },
  { name: 'workflow', hint: 'list / show / status / results <name>', runs: 'mgmt' },
  { name: 'loop', hint: 'list / status <id> research loops', runs: 'mgmt' },
  { name: 'session', hint: 'view — current research session aggregate', runs: 'mgmt' },
  { name: 'knowledge', hint: 'status / detail of the knowledge store', runs: 'mgmt' },
  { name: 'graph', hint: '[id] — knowledge graph view or node focus', runs: 'mgmt' },
];

/**
 * Autocomplete matches for the fragment after `/`, while the prompt is a
 * bare slash prefix (leading whitespace OK, no space after the verb yet).
 * Empty fragment returns the full catalog.
 */
export function filterSlashSuggestions(text: string, live: readonly { name: string; hint: string }[] = []): SlashSuggestion[] {
  const trimmed = text.trimStart();
  if (!trimmed.startsWith('/') || trimmed.includes(' ')) return [];
  const fragment = trimmed.slice(1).toLowerCase();
  const all = withLiveCatalog(live);
  return fragment ? all.filter((s) => s.name.startsWith(fragment)) : all;
}

/** VP-2d: the bridge's catalog (and the host's verbs) next to this surface's
 * own verbs; a name both have keeps this surface's entry (it runs here). */
function withLiveCatalog(live: readonly { name: string; hint: string }[]): SlashSuggestion[] {
  const own = new Set(WEBVIEW_SLASH_SUGGESTIONS.map((s) => s.name));
  const extra = live.filter((c) => !own.has(c.name)).map((c) => ({ name: c.name, hint: c.hint, runs: 'local' as const }));
  return [...WEBVIEW_SLASH_SUGGESTIONS, ...extra];
}
