/**
 * B3: detect unified-diff-shaped text and classify its lines so the
 * transcript can render `+`/`-`/`@@` lines with colors (Write/Edit
 * result parity with opencode's diff view).
 *
 * Pure helpers — JSX stays in ui/DiffBlock.tsx.
 */

export type DiffLineKind = "add" | "del" | "hunk" | "meta" | "ctx";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

function classifyOne(line: string): DiffLineKind {
  if (line.startsWith("@@")) return "hunk";
  if (
    line.startsWith("+++") ||
    line.startsWith("---") ||
    line.startsWith("diff ") ||
    line.startsWith("index ")
  ) {
    return "meta";
  }
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "ctx";
}

export function classifyDiffLines(text: string): DiffLine[] {
  return String(text)
    .split("\n")
    .map((line) => ({ kind: classifyOne(line), text: line }));
}

export interface DiffHeader {
  added: number;
  removed: number;
  files: string[];
}

/**
 * B8: one-line diff header — totals + touched files (from +++ b/path lines;
 * /dev/null dropped, de-duped, stable order). Returns null when empty.
 */
export function diffHeader(text: string): DiffHeader | null {
  let added = 0;
  let removed = 0;
  const files: string[] = [];
  for (const line of String(text).split("\n")) {
    if (line.startsWith("+++") && !line.startsWith("+++ /dev/null")) {
      const file = line
        .replace(/^\+\+\+\s+/, "")
        .replace(/^[ab]\//, "")
        .trim();
      if (file && !files.includes(file)) files.push(file);
      continue;
    }
    if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("diff ")) {
      continue;
    }
    if (line.startsWith("+")) added++;
    else if (line.startsWith("-")) removed++;
  }
  if (added === 0 && removed === 0) return null;
  return { added, removed, files };
}

/**
 * Diff-shaped requires at least one added AND one removed line, plus a hunk
 * header or meta line — avoids coloring +/- bullets in ordinary prose.
 */
export function isDiffShaped(text: string): boolean {
  const lines = classifyDiffLines(text);
  let hasAdd = false;
  let hasDel = false;
  let hasStructure = false;
  for (const line of lines) {
    if (line.kind === "add") hasAdd = true;
    else if (line.kind === "del") hasDel = true;
    else if (line.kind === "hunk" || line.kind === "meta") hasStructure = true;
  }
  return hasAdd && hasDel && hasStructure;
}
