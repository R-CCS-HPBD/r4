/**
 * Topic-ranked satellite knowledge-graph admission (Studio graph window).
 *
 * Before 2026-08-23 the rule was simply
 *     include ∪ (discovery && same-project active) − exclude − project default
 * so EVERY active graph sharing the current project mounted on the map as a
 * satellite — with an empty chat this looked like "every existing node
 * everywhere was drawn" (user report, same date). The new rule keeps the
 * same base algebra but ranks discovery-admitted graphs against the topic
 * of the current session: admission now requires term overlap between the
 * rolling user-message buffer and the graph's name/description. Explicit
 * includes always mount; with an empty topic buffer, discovery mounts
 * nothing (nothing to be selective about).
 *
 * Pure + exported so node:test can pin the admission table.
 */

export interface GraphLike {
  graph_id?: unknown;
  name?: unknown;
  description?: unknown;
  kind?: unknown;
  project_id?: unknown;
}

/** Stopwords that carry zero topic signal (small English/ja-accent-agnostic set). */
const STOPWORDS = new Set([
  "the", "and", "for", "are", "but", "not", "you", "all", "any", "can", "had",
  "her", "was", "one", "our", "out", "day", "get", "has", "him", "his", "how",
  "its", "may", "new", "now", "old", "see", "way", "who", "did", "this", "that",
  "with", "have", "from", "they", "know", "what", "been", "than", "them",
  "then", "when", "will", "would", "about", "into", "there", "could", "other",
  "which", "their", "some", "more", "only", "also", "very", "just", "over",
  "please", "show", "load", "graph", "knowledge", "hello", "help", "using",
  "use", "via", "data", "file",
]);

const MIN_TERM_LEN = 3;
const MAX_TERMS = 64;

/**
 * Extract lowercase topic terms from session message texts:
 * words ≥ MIN_TERM_LEN minus stopwords, deduped, capped.
 */
export function topicTerms(texts: readonly string[]): Set<string> {
  const terms = new Set<string>();
  for (const raw of texts) {
    if (typeof raw !== "string") continue;
    const words = raw
      .toLowerCase()
      .replace(/[`#*_~|[\](){}"'<>.!?,;:%$\/\\]/g, " ")
      .split(/\s+/);
    for (const word of words) {
      if (word.length < MIN_TERM_LEN || STOPWORDS.has(word)) continue;
      terms.add(word);
      if (terms.size >= MAX_TERMS) return terms;
    }
  }
  return terms;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(target: Record<string, unknown>, key: string): string {
  const value = target[key];
  return typeof value === "string" ? value : "";
}

/** Word-boundary-safe term match against a (already lowercase) text. */
function matches(text: string, term: string): boolean {
  // Length-prefix fast path, then bounded regexp per term (bounded: MAX_TERMS).
  const re = new RegExp(`\\b${escapeRegExp(term)}`);
  return re.test(text);
}

function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Non-empty terms against the graph's name + description (+ graph_id tail). */
export function graphMatchesTopic(graph: GraphLike, terms: ReadonlySet<string>): boolean {
  if (terms.size === 0 || !isRecord(graph)) return false;
  const haystack =
    `${stringField(graph, "name")} ${stringField(graph, "description")}`.toLowerCase();
  if (!haystack.trim()) return false;
  for (const term of terms) {
    if (matches(haystack, term)) return true;
  }
  return false;
}

/**
 * Discovery admission for one registry graph:
 *   include wins → always admitted
 *   exclude wins → never admitted (handled by caller before this)
 *   discovery-admitted ⇒ NEVER the project default (caller-skipped) AND
 *   (when terms exist) topic overlap; with no terms ⇒ kind-scoped
 *   fallback (research/domain satellites still surface "related graphs").
 */
export function discoveryAdmits(
  graph: GraphLike,
  terms: ReadonlySet<string>,
): boolean {
  if (terms.size > 0) {
    return graphMatchesTopic(graph, terms);
  }
  const kind = stringField(graph as Record<string, unknown>, "kind");
  return kind === "research" || kind === "domain";
}

/**
 * Full satellite ranking: include ∪ (∃terms && discovery-overlap ∧ same-project ∧ active)
 * − exclude − project default. Returns graph ids/names in stable catalog order.
 */
export function rankSatelliteGraphs(
  catalog: readonly unknown[],
  opts: {
    include: ReadonlySet<string>;
    exclude: ReadonlySet<string>;
    discovery: boolean;
    projectId?: string;
    terms: ReadonlySet<string>;
  },
): string[] {
  const { include, exclude, discovery, projectId, terms } = opts;
  const wanted: string[] = [];
  const graphs = Array.isArray(catalog) ? catalog : [];
  for (const graph of graphs) {
    if (!isRecord(graph)) continue;
    const id = stringField(graph, "graph_id");
    const name = stringField(graph, "name");
    const gid = id || name;
    if (!gid || exclude.has(id) || exclude.has(name)) continue;
    const sameProject =
      typeof graph.project_id === "string" && graph.project_id === projectId;
    const isDefaultOfProject = sameProject && graph.kind === "project";
    if (isDefaultOfProject) continue;
    if (include.has(id) || include.has(name)) {
      wanted.push(gid);
      continue;
    }
    if (discovery && sameProject && discoveryAdmits(graph, terms)) {
      wanted.push(gid);
    }
  }
  // Explicit includes unknown to the catalog still get attempted by name.
  for (const name of include) {
    if (exclude.has(name)) continue;
    const known = graphs.some(
      (graph) => isRecord(graph) && (graph.graph_id === name || graph.name === name),
    );
    if (!known && !wanted.includes(name)) wanted.push(name);
  }
  return wanted;
}
