/**
 * D4: markdown accent classification for the finalized per-line render path
 * of MarkdownBlock — headings, fenced code, blockquotes get theme accents.
 * Pure so the rules stay unit-testable.
 */

export type MarkdownLineKind = "heading" | "code" | "quote" | "plain";

/**
 * Classify each line of finalized markdown. Fenced-code state tracks across
 * lines; fence markers themselves render as code.
 */
export function accentLineKinds(lines: string[]): MarkdownLineKind[] {
  const kinds: MarkdownLineKind[] = [];
  let inFence = false;
  for (const raw of lines) {
    const line = raw.trimStart();
    if (line.startsWith("```")) {
      inFence = !inFence;
      kinds.push("code");
      continue;
    }
    if (inFence) {
      kinds.push("code");
      continue;
    }
    if (/^#{1,6}\s/.test(line)) {
      kinds.push("heading");
      continue;
    }
    if (line.startsWith(">")) {
      kinds.push("quote");
      continue;
    }
    kinds.push("plain");
  }
  return kinds;
}
