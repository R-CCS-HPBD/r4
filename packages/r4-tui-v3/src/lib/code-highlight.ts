/**
 * B8 — light syntax highlighting for transcript code fences.
 *
 * A pragmatic light tokenizer per language family (not full grammars):
 * strings and comments are tokenized by scanning, then keywords and numbers
 * are matched in the remaining code spans. Token output feeds per-span
 * colored rendering in the finalized MarkdownBlock path; the streaming
 * <markdown> path highlights natively.
 */

export type CodeTokenKind = "keyword" | "string" | "comment" | "number" | "default";

export interface CodeToken {
  text: string;
  kind: CodeTokenKind;
}

type Family = "js" | "py" | "sh" | "json" | "plain";

function familyForLang(lang: string): Family {
  const l = lang.toLowerCase();
  if (["js", "javascript", "jsx", "ts", "typescript", "tsx", "mjs", "cjs"].includes(l)) return "js";
  if (["py", "python", "pyw"].includes(l)) return "py";
  if (["sh", "bash", "zsh", "shell"].includes(l)) return "sh";
  if (["json", "yaml", "yml", "toml"].includes(l)) return "json";
  return "plain";
}

const KEYWORDS: Record<Exclude<Family, "plain" | "json">, RegExp> = {
  js: /\b(const|let|var|function|return|import|export|from|if|else|for|while|class|extends|new|await|async|try|catch|finally|throw|type|interface|readonly|typeof|instanceof|case|switch|break|continue|default|do|void|null|undefined|true|false|this|super|of|in|as|satisfies)\b/g,
  py: /\b(def|class|return|import|from|if|elif|else|for|while|try|except|finally|with|as|lambda|pass|break|continue|raise|in|is|not|and|or|yield|global|nonlocal|assert|del|async|await|True|False|None)\b/g,
  sh: /\b(if|then|else|elif|fi|for|while|until|do|done|case|esac|in|function|local|declare|export|readonly|echo|return|break|continue|shift|set|unset|source|alias|trap|exec|eval|cd|test|true|false)\b/g,
};

interface Span {
  start: number;
  end: number;
  kind: CodeTokenKind;
}

/** Consume a quoted string starting at index i; returns end (exclusive). */
function consumeString(line: string, start: number, quote: string): number {
  let i = start + 1;
  while (i < line.length) {
    if (line[i] === "\\") {
      i += 2;
      continue;
    }
    if (line[i] === quote) return i + 1;
    i++;
  }
  return line.length;
}

/** sh/py '#' starts a comment only at line start or after a word boundary. */
function hashIsComment(line: string, i: number): boolean {
  if (i === 0) return true;
  return !/[A-Za-z0-9_#$%&'"]/.test(line[i - 1]!);
}

function matchCommentAt(line: string, i: number, mark: string | null): boolean {
  if (mark === null || line[i] !== mark[0]) return false;
  if (mark.length > 1 && !line.startsWith(mark, i)) return false;
  if (mark === "#" && !hashIsComment(line, i)) return false;
  return true;
}

/** Split a line into string/comment spans, leaving everything else to code. */
function stringCommentSpans(line: string, family: Family): { spans: Span[]; gaps: Span[] } {
  const spans: Span[] = [];
  const gaps: Span[] = [];
  const quotes = family === "js" ? ['"', "'", "`"] : family === "json" ? ['"'] : ['"', "'"];
  const commentMark: string | null = family === "js" ? "//" : family === "json" ? null : "#";
  let i = 0;
  let gapStart = 0;
  const pushGap = (end: number) => {
    if (end > gapStart) gaps.push({ start: gapStart, end, kind: "default" });
  };
  while (i < line.length) {
    const ch = line[i]!;
    if (quotes.includes(ch)) {
      pushGap(i);
      const start = i;
      i = consumeString(line, i, ch);
      spans.push({ start, end: i, kind: "string" });
      gapStart = i;
      continue;
    }
    if (matchCommentAt(line, i, commentMark)) {
      pushGap(i);
      spans.push({ start: i, end: line.length, kind: "comment" });
      gapStart = line.length;
      i = line.length;
      continue;
    }
    i++;
  }
  pushGap(line.length);
  return { spans, gaps };
}

function keywordNumberSpans(gapText: string, gapStart: number, kwRe: RegExp | null): Span[] {
  const out: Span[] = [];
  if (kwRe) {
    kwRe.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = kwRe.exec(gapText))) {
      out.push({ start: gapStart + m.index, end: gapStart + m.index + m[0].length, kind: "keyword" });
    }
  }
  const numRe = /\b\d+(?:\.\d+)?\b/g;
  let m: RegExpExecArray | null;
  while ((m = numRe.exec(gapText))) {
    const start = gapStart + m.index;
    const end = start + m[0].length;
    // Skip if inside an already-found keyword span (defensive; both are wordbounded).
    if (out.some((s) => start >= s.start && end <= s.end)) continue;
    out.push({ start, end, kind: "number" });
  }
  return out.sort((a, b) => a.start - b.start);
}

/** Tokenize one source line for display; unknown families return a single token. */
export function tokenizeLine(line: string, lang: string): CodeToken[] {
  const family = familyForLang(lang);
  if (family === "plain" || line.length === 0) return [{ text: line, kind: "default" }];
  const { spans, gaps } = stringCommentSpans(line, family);
  const codeSpans: Span[] = gaps.flatMap((gap) =>
    keywordNumberSpans(
      line.slice(gap.start, gap.end),
      gap.start,
      family in KEYWORDS ? KEYWORDS[family as Exclude<Family, "plain" | "json">] : null,
    ),
  );
  const spansAll = [...spans, ...codeSpans].sort((a, b) => a.start - b.start);
  const tokens: CodeToken[] = [];
  let cursor = 0;
  for (const span of spansAll) {
    if (span.start > cursor) tokens.push({ text: line.slice(cursor, span.start), kind: "default" });
    tokens.push({ text: line.slice(span.start, span.end), kind: span.kind });
    cursor = span.end;
  }
  if (cursor < line.length) tokens.push({ text: line.slice(cursor), kind: "default" });
  return tokens.length > 0 ? tokens : [{ text: line, kind: "default" }];
}

/** Map a file extension / path suffix → tokenizer language family name.
 * Used by work-output details to pick a colorizer per code row without
 * requiring a fence marker on the row itself. */
export function langForPath(path: string | undefined): string | null {
  if (!path) return null;
  const lower = path.toLowerCase();
  if (lower.endsWith(".py") || lower.endsWith(".pyw")) return "py";
  if (/\.(m?js|cjs|jsx|ts|tsx)$/.test(lower)) return "js";
  if (/\.(sh|bash|zsh)$/.test(lower)) return "sh";
  if (/\.(json|yaml|yml|toml)$/.test(lower)) return "json";
  return null;
}

/**
 * Per-line fence language: index → lang while INSIDE a fenced code block
 * (both fence marker lines themselves map to null so ``` markers render
 * as plain code-colored rows).
 */
export function fenceCodeLangs(lines: string[]): (string | null)[] {
  const langs: (string | null)[] = [];
  let inFence = false;
  let lang: string | null = null;
  for (const raw of lines) {
    const trimmed = raw.trimStart();
    if (trimmed.startsWith("```")) {
      if (inFence) {
        inFence = false;
        langs.push(null);
      } else {
        inFence = true;
        lang = trimmed.slice(3).trim() || null;
        langs.push(null);
      }
      continue;
    }
    langs.push(inFence ? lang : null);
  }
  return langs;
}
