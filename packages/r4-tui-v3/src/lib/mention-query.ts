/**
 * The `@file` token being typed at the end of a draft (A1, `#L10-L20` ranges).
 * Pure, no imports (ADR-103 D-3, 2026-11-01): the console's `file-mentions.ts`
 * re-exports it and the VS Code webview's composer imports it.
 */

/** Extract a mention token at the END of draft text, if one is active. */
/** A1: `@file#L10-L20` range syntax — trailing range parsed and re-attached on completion. */
const RANGE_SUFFIX_RE = /^#L(\d+)(?:-(?:L)?(\d+))?$/;

export interface MentionQuery {
  /** Path fragment used for ranking (range suffix stripped). */
  query: string;
  /** Range suffix like `#L10-L20` (empty when absent), re-attached on completion. */
  range: string;
  /** Start index of the `@` token inside the draft. */
  start: number;
}

/** Split a trailing #L10-L20 range off the path part. An incomplete range
 * fragment (e.g. `#L`, `#L10-`) keeps the popup closed — treated as prose. */
function splitRange(token: string): { pathPart: string; range: string } | null {
  const hashIdx = token.indexOf("#");
  if (hashIdx === -1) return { pathPart: token, range: "" };
  const candidate = token.slice(hashIdx);
  if (!RANGE_SUFFIX_RE.exec(candidate)) return null;
  return { pathPart: token.slice(0, hashIdx), range: candidate };
}

/** An `@path` token (with an optional `#L10-L20`) at the end of the draft. */
const MENTION_TOKEN_RE = /(^|\s)(@[\w.\-~/]*(?:#L\d+(?:-(?:L)?\d+)?)?)$/;

export function extractMentionQuery(draft: string): MentionQuery | null {
  const match = MENTION_TOKEN_RE.exec(draft);
  if (!match) return null;
  const token = match[2];
  // `@@` or email-like tokens are not mentions; a bare "@" stays literal.
  if (token.includes("@@") || token.length < 2) return null;
  const split = splitRange(token);
  if (!split) return null;
  return {
    query: split.pathPart.slice(1),
    range: split.range,
    start: match.index + match[1].length,
  };
}
