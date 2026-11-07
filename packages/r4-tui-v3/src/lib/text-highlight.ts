/**
 * Semantic line colorizer (opencode parity) — spots library/project/general
 * names inside plain text and assigns dedicated colors:
 *
 *   paths (/a/b, ./x, ~/y)  → cyan      status errors (error/failed/denied) → red
 *   flags (--foo, -x)       → amber     status ok (ok/done/passed/ready)    → green
 *   durations/sizes (12s, 3MB) → violet status warn (warning/pending)       → amber
 *   URLs / $VARS            → blue      backtick code                     → teal
 *
 * Pure tokenizer — no JSX — so the span list is unit-testable.
 */

import { palette } from "../themes";

export interface HighlightSpan {
  text: string;
  fg?: string;
}

/** Ordered alternation — the FIRST matching category wins per token. */
const TOKEN_RE = new RegExp(
  [
    // 0: markdown-emphasis — bold / inline code / inline-italic (picked first so
    // the agent's structured leads already scan at a glance).
    "(?<bold>\\*\\*[^*\\n]+?\\*\\*)",
    "(?<it>__[^_\\n]+?__)",
    // 1: URLs (must precede paths so http://… is not split into path chunks)
    "(?<url>https?://[A-Za-z0-9_\"%+&#@~.:=?/,\\-]+)",
    // 2: filesystem paths (2+ segments, optional ./ ~/ / prefix)
    "(?<path>(?:\\.{1,2}/|~/|/)?[\\w.\\-+]*(?:/[\\w.\\-+]+)+)",
    // 3: backtick code spans
    "(?<tick>`[^`\\n]+`)",
    // 4: $ENV_VARS
    "(?<env>\\$\\{?[A-Z_][A-Z0-9_]*\\}?)",
    // 5: long/short flags (not bare digits, not a leading $)
    "(?:^|(?<=[\\s:(,]))(?<flag>-{1,2}[a-zA-Z][\\w-]*)",
    // 6: durations / sizes / counts with unit suffix
    "(?<num>\\d+(?:\\.\\d+)?(?:ms|s|m|h|min|B|KB|MB|GB|TB|tok|x)\\b)",
    // 7: status words
    "(?<err>\\b(?:error|failed|failure|denied|blocked|exception|traceback|fatal|crash)\\b)",
    "(?<ok>\\b(?:ok|okay|done|passed|pass|success|succeeded|completed|complete|ready|healthy|green)\\b)",
    "(?<warn>\\b(?:warn|warning|skipped|pending|cancelled|canceled|stale|degraded)\\b)",
  ].join("|"),
  "gi",
);

/**
 * Live-read palette group map (T5 topic-16): theme switches at runtime must
 * swap these hues too — a frozen capture at module eval loses the new scheme.
 */
const GROUP_FG_RESOLVERS: Record<string, () => string | undefined> = {
  bold: () => palette.mdHeading,
  it: () => palette.fgDim,
  url: () => palette.statusInfo,
  path: () => palette.knowledge,
  tick: () => palette.mdCode,
  env: () => palette.accent2,
  flag: () => palette.statusWarn,
  num: () => palette.accent,
  err: () => palette.statusErr,
  ok: () => palette.statusOk,
  warn: () => palette.statusWarn,
};

const GROUP_NAMES = new Set(Object.keys(GROUP_FG_RESOLVERS));

function groupFg(groups: Record<string, string | undefined>): string | undefined {
  for (const key of GROUP_NAMES) {
    if (groups[key] !== undefined) {
      return GROUP_FG_RESOLVERS[key]?.();
    }
  }
  return undefined;
}

function tokenizeIter(text: string, spans: HighlightSpan[], last: number): number {
  TOKEN_RE.lastIndex = 0;
  for (let match = TOKEN_RE.exec(text); match !== null; match = TOKEN_RE.exec(text)) {
    if (match.index > last) spans.push({ text: text.slice(last, match.index) });
    spans.push({ text: match[0], fg: groupFg(match.groups ?? {}) });
    last = match.index + match[0].length;
    // zero-width safety (never loop forever on empty matches)
    TOKEN_RE.lastIndex = match[0] === "" ? TOKEN_RE.lastIndex + 1 : TOKEN_RE.lastIndex;
  }
  return last;
}

/** Split one line into colored spans. Plain runs keep fg undefined so callers
 *  fall back to their own base color. Never throws — worst case = one span. */
export function highlightLine(line: string): HighlightSpan[] {
  const text = String(line ?? "");
  if (text === "") return [{ text: "" }];
  const spans: HighlightSpan[] = [];
  const last = tokenizeIter(text, spans, 0);
  if (last < text.length) spans.push({ text: text.slice(last) });
  return spans;
}
