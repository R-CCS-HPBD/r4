/**
 * Stabilization 2026-10-21 (render lane) — every width inside a chat cell
 * derives from the REAL text column.
 *
 * The 2026-10-19 visibility wave made prose wrap to `bodyWidth()`, which
 * turned three width mismatches from "clipped tail" into "words silently
 * cut out of the middle of a complete-looking paragraph":
 *   wip-eval#0  bodyWidth had a 40-col floor → at chat columns < 46 every
 *               wrapped row was overlong and the engine dropped words;
 *   wip-eval#1  chatColumnWidth ignored workspace.paneWidthOverride → a
 *               dragged pane left the wrap at the DEFAULT dock width;
 *   wip-eval#2  wrapProseLine counted UTF-16 units → Japanese rows were
 *               twice the column and their tails vanished;
 *   wip-eval#3  diffs still got the full chat column after the cell grew a
 *               spine + padding → the gutter separator was shrunk away.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { ChatPanel } from "../src/components/chat";
import { Shell } from "../src/layout/shell";
import { wrapProseLine, displayWidth } from "../src/lib/markdown-wrap";
import { chatColumnWidth } from "../src/lib/term-width";
import { resetWorkspace, actions, setWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

const WORDS =
  "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee zulu END";
const JA =
  "計算結果は正常に収束しました。出力ファイルは指定されたディレクトリに保存されています。次の手順として可視化を実行してください。最後END";

function reply(content: string): void {
  setWorkspace("chatMessages", [
    { id: "a1", role: "assistant", content, timestamp: 2, model: "m1" },
  ] as never);
}

async function frameOf(node: () => any, width: number, height = 30): Promise<string> {
  const setup = await testRender(node, { width, height });
  await setup.renderOnce();
  await setup.renderOnce();
  await setup.renderOnce();
  const frame = setup.captureCharFrame();
  setup.renderer.destroy();
  return frame;
}

function missingWords(frame: string): string[] {
  return WORDS.split(" ").filter((w) => !new RegExp(`\\b${w}\\b`).test(frame));
}

describe("prose wrap column", () => {
  for (const width of [30, 44, 45, 46]) {
    it(`narrow chat column ${width}: no word is dropped (wip-eval#0)`, async () => {
      reply(WORDS);
      expect(missingWords(await frameOf(() => <ChatPanel />, width))).toEqual([]);
    });
  }

  it("resized pane: wraps to the dragged chat column (wip-eval#1)", async () => {
    reply(WORDS);
    setWorkspace("paneView", "logs" as never);
    setWorkspace("paneWidthOverride", 80 as never);
    const frame = await frameOf(() => <Shell />, 120, 40);
    expect(missingWords(frame)).toEqual([]);
  });

  it("wrapped user message keeps continuation rows and inline-code context (slash-c#3)", async () => {
    const u =
      "I am sharing the tail of my terminal pane (the `/term` PTY embedded in the TUI) — the long output scrolls off so it is not visible END-CODE";
    setWorkspace("chatMessages", [{ id: "u1", role: "user", content: u, timestamp: 1 }] as never);
    for (const width of [100, 150]) {
      const flat = (await frameOf(() => <ChatPanel />, width)).replace(/[\s┃│]+/g, " ");
      expect(flat).toContain("terminal pane (the `/term` PTY embedded");
      expect(flat).toContain("not visible END-CODE");
    }
  });

  it("chatColumnWidth subtracts the dragged pane width", () => {
    expect(chatColumnWidth(120, true, 80)).toBe(40);
    expect(chatColumnWidth(120, true, null)).toBe(Math.floor(120 * 0.62));
    expect(chatColumnWidth(120, false, 80)).toBe(120);
  });

  it("chatColumnWidth prefers CURRENT measured pane bounds, ignores stale ones", () => {
    expect(chatColumnWidth(160, true, null, { x: 111, width: 49 })).toBe(111);
    // stale: measured under a 260-col terminal → estimate
    expect(chatColumnWidth(160, true, null, { x: 180, width: 80 })).toBe(Math.floor(160 * 0.55));
    expect(chatColumnWidth(160, false, null, { x: 111, width: 49 })).toBe(160);
  });

  it("docked pane: prose fills the MEASURED chat column", async () => {
    reply(WORDS + " " + WORDS);
    setWorkspace("paneView", "logs" as never);
    const frame = await frameOf(() => <Shell />, 160, 40);
    expect(missingWords(frame)).toEqual([]);
    const first = frame.split("\n").find((l) => l.includes("alpha bravo")) ?? "";
    const prose = first.slice(first.indexOf("alpha")).split("┃")[0].trimEnd();
    // c3-tui D-2 (2026-10-21): the pane now lays out at its designed 45%, so
    // the measured chat column is the 55% one (88 - 6 = 82 text cols). Before,
    // the engine squeezed the pane to ~30% and this pinned the wider column.
    expect(prose.length).toBeGreaterThan(74);
    expect(prose.length).toBeLessThanOrEqual(82);
  });
});

describe("CJK display width (wip-eval#2)", () => {
  it("wrapProseLine rows never exceed the column in DISPLAY columns", () => {
    const rows = wrapProseLine(JA, 40);
    expect(rows.length).toBeGreaterThan(1);
    for (const row of rows) expect(displayWidth(row)).toBeLessThanOrEqual(40);
    expect(rows.join("")).toBe(JA);
  });

  it("never starts a row with closing punctuation (kinsoku)", () => {
    for (const width of [9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]) {
      for (const row of wrapProseLine(JA, width)) expect(row).not.toMatch(/^[、。]/);
    }
  });

  it("mixed JA/EN keeps every token", () => {
    const line = "計算は ジョブ finished で完了しました。結果を確認してください。";
    const rows = wrapProseLine(line, 20);
    for (const row of rows) expect(displayWidth(row)).toBeLessThanOrEqual(20);
    expect(rows.join("").replace(/ /g, "")).toBe(line.replace(/ /g, ""));
  });

  it("a Japanese reply shows its last sentence in the transcript", async () => {
    reply(JA);
    const frame = await frameOf(() => <ChatPanel />, 100);
    const flat = frame.replace(/[\s┃│]/g, "");
    expect(flat).toContain("次の手順として可視化を実行してください。");
    expect(flat).toContain("最後END");
  });
});

describe("diff width inside the cell (wip-eval#3)", () => {
  const long = "abcdefghij ".repeat(16).trim();
  const DIFF = [
    "--- a/src/x.txt",
    "+++ b/src/x.txt",
    "@@ -1,2 +1,2 @@",
    `-old ${long} TAILOLD`,
    `+new ${long} TAILNEW`,
    " ctx",
  ].join("\n");

  it("unified diff rows keep their gutter separator at 80 cols", async () => {
    reply(DIFF);
    const frame = await frameOf(() => <ChatPanel />, 80);
    const del = frame.split("\n").find((l) => l.includes("old abcdefghij"));
    const add = frame.split("\n").find((l) => l.includes("new abcdefghij"));
    // Gutter " old new │ " + sign "- " + text. The overlong rows shrank the
    // gutter: "1      - old" (WIP, separator gone) / "│-old" (base).
    expect(del).toMatch(/│ - old abcdefghij/);
    expect(add).toMatch(/│ \+ new abcdefghij/);
    // span-aware truncation marks the cut instead of the edge eating it
    expect(del).toContain("…");
  });
});

describe("tables fit the cell text column (resize-render#7)", () => {
  const TABLE = [
    "| id | name | status | gpu | memory | node |",
    "|---|---|---:|---|---|---|",
    "| 1 | qwen3-coder-480b-instruct | running | H200x8 | 1.2TB | gpu-node-0001.cluster |",
    "| 2 | tiny | idle | - | 8GB | n2 |",
  ].join("\n");

  for (const width of [60, 80]) {
    it(`every column and the right border stay visible at ${width} cols`, async () => {
      reply(TABLE);
      const frame = await frameOf(() => <ChatPanel />, width);
      const grid = frame.split("\n").filter((l) => /[┌├└]|│ (id|1 |2 )/.test(l));
      expect(grid.length).toBe(6);
      for (const row of grid) expect(row.trimEnd()).toMatch(/[┐┤┘│]$/);
      const header = grid.find((l) => l.includes("│ id"))!;
      for (const name of ["id", "name", "stat", "gpu", "mem", "node"]) expect(header).toContain(name);
    });
  }

  it("CJK cells pad by display width so the grid stays aligned", async () => {
    reply(["| 名前 | 状態 |", "|---|---|", "| 計算ノード | 実行中 |", "| a | b |"].join("\n"));
    const frame = await frameOf(() => <ChatPanel />, 80);
    const grid = frame.split("\n").filter((l) => /[┌├└]|│ (名前|計算|a )/.test(l));
    const ends = grid.map((l) => Bun.stringWidth(l.trimEnd()));
    expect(new Set(ends).size).toBe(1);
  });
});
