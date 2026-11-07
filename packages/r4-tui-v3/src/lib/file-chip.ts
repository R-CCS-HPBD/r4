/**
 * A5: file-kind paste chips ([Image N] / [PDF N] / [SVG N]).
 * TS port of r4/companion-tui-js/src/file-chip.mjs — identical semantics.
 * Buffer & payload stay verbatim; only user-bubble display chips them.
 */

const FILE_TOKEN_RE =
  /(^|[\s@"'([{])([\w~.\-/\\]+\.(?:png|jpe?g|gif|bmp|webp|svg|pdf))(?=$|[\s"'()\]}>,.;:?])/gim;

const KIND_BY_EXT: Record<string, string> = {
  png: 'Image',
  jpg: 'Image',
  jpeg: 'Image',
  gif: 'Image',
  bmp: 'Image',
  webp: 'Image',
  svg: 'SVG',
  pdf: 'PDF',
};

function kindForPath(path: string): string {
  const m = String(path).toLowerCase().match(/\.([a-z0-9]+)$/);
  return KIND_BY_EXT[m?.[1] ?? ''] ?? 'File';
}

export interface FileChip {
  path: string;
  kind: string;
}

export function detectFileChips(text: string | undefined): FileChip[] {
  if (!text) return [];
  const seen = new Map<string, FileChip>();
  const re = new RegExp(FILE_TOKEN_RE.source, FILE_TOKEN_RE.flags);
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const path = match[2];
    if (!path || seen.has(path)) continue;
    seen.set(path, { path, kind: kindForPath(path) });
  }
  return [...seen.values()];
}

export function chipifyFileDisplayText(text: string): string {
  const chips = detectFileChips(text);
  if (chips.length === 0) return String(text);
  const chipsByKind = new Map<string, number>();
  let out = String(text);
  for (const chip of chips) {
    const count = (chipsByKind.get(chip.kind) ?? 0) + 1;
    chipsByKind.set(chip.kind, count);
    out = out.split(chip.path).join(`[${chip.kind} ${count}]`);
  }
  return out;
}
