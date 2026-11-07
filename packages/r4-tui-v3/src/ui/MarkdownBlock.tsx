/**
 * Transcript markdown block (single renderer for streaming + finalized).
 *
 * History: OpenTUI 0.3.4's <markdown> renders streaming content with real
 * block semantics, but (a) drops unflushed fences, (b) drops paragraphs for
 * finalized content, and — verified by captureSpans probe 2026-09-30 —
 * (c) paints fenced code with ZERO syntax color and swallows fence content
 * into blank rows unless handed a TreeSitterClient the app never wired.
 * The per-line path keeps every byte and, with the shiki-backed
 * CodeFenceBlock below, paints real token colors in both modes, so both
 * streaming and finalized messages now use it. The native <markdown> path
 * is kept behind `streamingMarkdown` as an opt-in escape hatch.
 */
import { For, Show, createMemo, createSignal } from "solid-js";
import { SyntaxStyle } from "@opentui/core";
import { palette } from "../themes";
import { HighlightText } from "./HighlightText";
import { accentLineKinds, type MarkdownLineKind } from "../lib/markdown-accents";
import { splitTableSegments } from "../lib/markdown-tables";
import { TableBlock } from "./TableBlock";
import { tokenizeLine, type CodeTokenKind } from "../lib/code-highlight";
import { wrapProseLine } from "../lib/markdown-wrap";
import { terminalSafe } from "../lib/terminal-safe";
import { hiddenRowsLabel, windowRows, type RowSlicer } from "../lib/render-window";
import {
  highlightWithShiki,
  normalizeShikiLang,
  shikiReady,
  type ShikiSpan,
} from "../lib/code-shiki";

const KIND_FG: Record<MarkdownLineKind, string | undefined> = {
  heading: palette.mdHeading,
  code: palette.mdCode,
  quote: palette.mdQuote,
  plain: undefined,
};

// B8: token colors for the regex fallback path.
const TOKEN_FG: Record<CodeTokenKind, string> = {
  keyword: palette.mdHeading,
  string: palette.statusOk,
  comment: palette.fgDim,
  number: palette.knowledge,
  default: palette.mdCode,
};

// Code rows: one <text> per token, flexShrink=0 + wrapMode="none" so an
// overwide row clips at the right edge instead of Yoga shrinking every token
// (identifiers vanished, only operators stayed — stabilization 2026-10-21).

/** One code row via the regex fallback tokenizer (pre-shiki / unknown lang). */
function RegexCodeLine(props: { text: string; lang: string }) {
  return (
    <box height={1} width="100%" flexDirection="row" overflow="hidden" paddingLeft={1}>
      <For each={tokenizeLine(props.text, props.lang)}>
        {(token) => (
          <text fg={TOKEN_FG[token.kind]} flexShrink={0} wrapMode="none">
            {token.text}
          </text>
        )}
      </For>
    </box>
  );
}

/** One code row from shiki spans (truecolor fg straight from the theme). */
function ShikiCodeLine(props: { spans: ShikiSpan[] }) {
  return (
    <box height={1} width="100%" flexDirection="row" overflow="hidden" paddingLeft={1}>
      <For each={props.spans}>
        {(span) => (
          <text fg={span.fg ?? palette.mdCode} flexShrink={0} wrapMode="none">
            {span.text}
          </text>
        )}
      </For>
    </box>
  );
}

/**
 * One fenced code block: language chip header + shiki-colored rows (regex
 * fallback until the engine reports ready). Readiness is reactive so the
 * first paint upgrades in place once grammars finish loading. Exported for
 * the modified-files card's styled write-content view.
 */
export function CodeFenceBlock(props: { lang: string | null; lines: string[] }) {
  const lang = () => normalizeShikiLang(props.lang);
  const shikiLines = createMemo(() => {
    if (!shikiReady()) return null;
    const l = lang();
    if (!l) return null;
    return highlightWithShiki(props.lines, l);
  });
  return (
    <box
      flexDirection="column"
      width="100%"
      overflow="hidden"
      backgroundColor={palette.bgPanel}
    >
      <box height={1} width="100%" overflow="hidden" paddingLeft={1}>
        <text fg={palette.fgDim}>{`▍${props.lang ?? "code"}`}</text>
      </box>
      <For each={props.lines}>
        {(line, i) => {
          const spans = shikiLines()?.[i()];
          return spans ? (
            <ShikiCodeLine spans={spans} />
          ) : (
            <RegexCodeLine text={line} lang={lang() ?? "plain"} />
          );
        }}
      </For>
    </box>
  );
}

let _syntaxStyle: SyntaxStyle | null = null;

export function markdownSyntaxStyle(): SyntaxStyle {
  if (!_syntaxStyle) _syntaxStyle = SyntaxStyle.create();
  return _syntaxStyle;
}

/**
 * Streaming block mode. "top-level" keeps each completed markdown block a
 * SEPARATE renderable — a finished `##` heading is painted once and never
 * touched again while later text streams in below it. Only relevant to the
 * opt-in native <markdown> escape hatch.
 */
export const STREAMING_BLOCK_MODE = "top-level" as const;

/** Fence-first chunk: consecutive code-fence rows become ONE block item so
 * shiki can tokenize multi-line constructs; other rows keep per-line flow. */
type Segment =
  | { kind: "code"; lang: string | null; lines: string[] }
  | { kind: "rows"; lines: string[] };

function splitFenceSegments(lines: string[]): Segment[] {
  const out: Segment[] = [];
  let rows: string[] = [];
  let code: string[] | null = null;
  let lang: string | null = null;
  const flushRows = () => {
    if (rows.length > 0) out.push({ kind: "rows", lines: rows });
    rows = [];
  };
  for (const raw of lines) {
    const trimmed = raw.trimStart();
    if (trimmed.startsWith("```")) {
      if (code !== null) {
        out.push({ kind: "code", lang, lines: code });
        code = null;
        lang = null;
      } else {
        flushRows();
        code = [];
        lang = trimmed.slice(3).trim() || null;
      }
      continue;
    }
    if (code !== null) code.push(raw);
    else rows.push(raw);
  }
  flushRows();
  if (code !== null) out.push({ kind: "code", lang, lines: code });
  return out;
}

type RenderItem =
  | { type: "line"; fg: string | undefined; text: string; kind: MarkdownLineKind }
  | { type: "table"; table: import("../lib/markdown-tables").TableBlock }
  | { type: "code"; lang: string | null; lines: string[] };

/** Rows of one render item: a code block adds its language header row, a
 * table its header and rule rows. take() cuts an item to rows [from, to). */
const ITEM_ROWS: RowSlicer<RenderItem> = {
  rows(item) {
    if (item.type === "code") return item.lines.length + 1;
    if (item.type === "table") return item.table.rows.length + 2;
    return 1;
  },
  take(item, from, to) {
    if (item.type === "code") return { ...item, lines: item.lines.slice(Math.max(0, from - 1), Math.max(0, to - 1)) };
    if (item.type === "table") {
      return { ...item, table: { ...item.table, rows: item.table.rows.slice(Math.max(0, from - 2), Math.max(0, to - 2)) } };
    }
    return item;
  },
};

/** Emit one prose line as render item(s) — wrapped at `wrap` columns when
 * wrap >= 8, verbatim otherwise (CCN guard: kept out of the items memo). */
function pushLineItems(
  out: RenderItem[],
  wrap: number,
  fg: string | undefined,
  text: string,
  kind: MarkdownLineKind,
): void {
  if (wrap >= 8) {
    for (const seg of wrapProseLine(text, wrap)) out.push({ type: "line", fg, text: seg, kind });
    return;
  }
  out.push({ type: "line", fg, text, kind });
}

export function MarkdownBlock(props: {
  content: string;
  streaming?: boolean;
  dimFg?: string;
  baseFg?: string;
  streamingMarkdown?: boolean;
  /** Wrap column for prose lines (visibility wave 2026-10-19): ≥8 wraps
   * prose at word boundaries instead of clipping it at the right edge;
   * absent/0 keeps the legacy one-row-per-source-line contract (tests,
   * layout-stable embedders). Code fences and tables never wrap. */
  wrapWidth?: number;
}) {
  // Leading blank rows paint as an empty gutter between the message header
  // (role/@model) and the reply — LLMs commonly open with "\n". Trim them
  // (display-only; the stored content is untouched). wave(15) reapplied.
  // Untrusted text (replies, tool and `!` output): no control character may
  // reach the terminal (c2-tui 2026-10-21, OSC 52 clipboard writes).
  const content = createMemo(() => terminalSafe(props.content));
  const lines = createMemo(() => {
    const all = content().split("\n");
    let start = 0;
    while (start < all.length - 1 && all[start].trim() === "") start++;
    return all.slice(start);
  });
  // Wrap column for prose rows; 0 keeps the legacy per-line contract.
  // Outside the memo: the fallback branch counts toward its CCN (≤ 9).
  const wrap = () => Math.floor(props.wrapWidth ?? 0);
  const items = createMemo<RenderItem[]>(() => {
    const out: RenderItem[] = [];
    const wrapWidth = wrap();
    for (const segment of splitFenceSegments(lines())) {
      if (segment.kind === "code") {
        out.push({ type: "code", lang: segment.lang, lines: segment.lines });
        continue;
      }
      const kinds = accentLineKinds(segment.lines);
      let cursor = 0;
      for (const part of splitTableSegments(segment.lines)) {
        if (part.kind === "table") {
          out.push({ type: "table", table: part.table });
          cursor += part.table.rows.length;
        } else {
          for (const line of part.lines) {
            const kind = kinds[cursor] ?? "plain";
            pushLineItems(out, wrapWidth, KIND_FG[kind], line, kind);
            cursor++;
          }
        }
      }
    }
    return out;
  });
  // Huge blocks render a head + tail preview (lib/render-window, D-1); the
  // marker row between them pages more rows in.
  const [pages, setPages] = createSignal(0);
  const shown = createMemo(() => windowRows(items(), ITEM_ROWS, pages()));
  // Native <markdown> is an explicit opt-in (escape hatch); the per-line
  // path above is the default for streaming AND finalized content.
  const useMarkdown = () => props.streaming === true && props.streamingMarkdown === true;
  const renderItem = (item: RenderItem) => <MarkdownItem item={item} wrap={wrap()} baseFg={props.baseFg} />;

  return (
    // Empty content renders NOTHING (no placeholder row) — a blank row
    // between the @model header and the pending reply read as a layout bug.
    <Show when={content().length > 0}>
      <Show
        when={useMarkdown()}
        fallback={
          <>
            <For each={shown().head}>{renderItem}</For>
            <Show when={shown().hiddenRows > 0}>
              <box
                height={1}
                width="100%"
                overflow="hidden"
                onMouseDown={() => {
                  if (shown().canExpand) setPages((n) => n + 1);
                }}
              >
                <text fg={props.dimFg ?? palette.fgDim}>{hiddenRowsLabel(shown().hiddenRows, shown().canExpand)}</text>
              </box>
            </Show>
            <For each={shown().tail}>{renderItem}</For>
          </>
        }
      >
        <markdown
          content={content()}
          streaming={true}
          internalBlockMode={STREAMING_BLOCK_MODE}
          syntaxStyle={markdownSyntaxStyle()}
        />
      </Show>
    </Show>
  );
}

/** One render item: table, code fence, colorized prose or an accent row. */
function MarkdownItem(props: { item: RenderItem; wrap: number; baseFg?: string }) {
  const item = props.item;
  if (item.type === "table") return <TableBlock table={item.table} width={props.wrap >= 8 ? props.wrap : undefined} />;
  if (item.type === "code") return <CodeFenceBlock lang={item.lang} lines={item.lines} />;
  // Semantic colorizer: paths / flags / statuses inside prose
  if (item.kind === "plain") return <HighlightText text={item.text} baseFg={props.baseFg ?? item.fg} />;
  return (
    <box height={1} width="100%" overflow="hidden">
      <text fg={item.fg}>{item.text}</text>
    </box>
  );
}
