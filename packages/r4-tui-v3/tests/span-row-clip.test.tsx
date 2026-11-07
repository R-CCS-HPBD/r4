/**
 * Stabilization 2026-10-21 (render lane) — span rows clip, never shrink.
 *
 * HighlightText and the fenced-code rows paint one sibling <text> per
 * colour span inside a height-1 flex row. The spans kept Yoga's default
 * flexShrink=1, so a row wider than its box shrank EVERY span in
 * proportion: each span lost its own tail (and word-wrapped onto a hidden
 * second line), so words vanished mid-line and code read as different code
 * ("resul=          +          *"). Findings chat#4, connect#3, composer#8,
 * panes-overlays#2, resize-render#2, slash-a#2, slash-b#5, slash-c#3.
 *
 * Contract pinned here: an overwide span row is clipped at the RIGHT edge
 * only — every column that is painted shows the source text verbatim.
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { ChatPanel } from "../src/components/chat";
import { HighlightText } from "../src/ui/HighlightText";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";
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

const CODE =
  "result = alpha_value + beta_value * gamma_value - delta_value / epsilon_value  # comment here";

async function firstRow(node: () => any, width: number): Promise<string> {
  const setup = await testRender(node, { width, height: 6 });
  await setup.renderOnce();
  const rows = setup.captureCharFrame().split("\n").filter((l) => l.trim() !== "");
  setup.renderer.destroy();
  return rows[0] ?? "";
}

describe("HighlightText overwide rows", () => {
  it("keeps the visible prefix verbatim (no per-span shrink)", async () => {
    // highlightLine splits this into several coloured spans (/command,
    // --flags, a path) — the exact shape that garbled in the transcript.
    const text =
      "unknown flag --kind. usage: /source add <name> --url <url> [--api-key <key> | --api-key-env <VAR>] and /tmp/results/run-2026/out.log";
    const row = await firstRow(() => <HighlightText text={text} />, 60);
    expect(row.length).toBe(60);
    expect(text.startsWith(row)).toBe(true);
  });

  it("chat-unavailable error row keeps its words", async () => {
    const text =
      "[Chat unavailable] LLM provider returned a server error (HTTP 500: server error). The bridge and Companion are fine — the upstream model backend failed.";
    const row = await firstRow(() => <HighlightText text={text} />, 80);
    expect(text.startsWith(row.trimEnd())).toBe(true);
    expect(row).toContain("LLM provider returned a server error");
  });
});

describe("fenced code rows overwide", () => {
  it("clips identifiers at the right edge instead of shrinking each token", async () => {
    const code = CODE;
    const bt = String.fromCharCode(96).repeat(3);
    const setup = await testRender(
      () => <MarkdownBlock content={`${bt}python\n${code}\n${bt}`} streaming={false} />,
      { width: 60, height: 6 },
    );
    await setup.renderOnce();
    const rows = setup.captureCharFrame().split("\n").filter((l) => l.trim() !== "");
    setup.renderer.destroy();
    // rows[0] is the lang chip; rows[1] the code row (paddingLeft 1).
    const painted = rows[1].slice(1);
    expect(code.startsWith(painted)).toBe(true);
    expect(painted).toContain("alpha_value + beta_value");
  });
});

describe("tool-call code rows overwide (ToolCodeLine)", () => {
  // keyword / string / number / comment → several sibling token spans.
  const JS =
    'const result = alpha_value + "beta_value" * 42 - gamma_value / epsilon_value; // comment here';
  it("an expanded tool call code row keeps its identifiers", async () => {
    setWorkspace("verboseTranscript", true);
    setWorkspace("chatMessages", [
      {
        id: "a1",
        role: "assistant",
        content: "ran it",
        timestamp: 2,
        model: "m1",
        toolCalls: [{ status: "ok", name: "node", text: "node", args: { code: JS } }],
      },
    ] as never);
    const setup = await testRender(() => <ChatPanel />, { width: 60, height: 30 });
    await setup.renderOnce();
    await setup.renderOnce();
    const row = setup.captureCharFrame().split("\n").find((l) => l.includes("» ")) ?? "";
    setup.renderer.destroy();
    const painted = row.slice(row.indexOf("» ") + 2).trimEnd();
    expect(painted.length).toBeGreaterThan(20);
    expect(JS.startsWith(painted)).toBe(true);
  });
});
