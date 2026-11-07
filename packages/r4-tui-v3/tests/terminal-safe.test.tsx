/**
 * c2-tui (2026-10-21, PTY probes p3/p4): text from a model reply or a `!`
 * command reached the terminal with its control sequences intact. A reply
 * carrying "ESC ] 52 ; c ; <base64> BEL" was written raw — an OSC 52
 * clipboard write any prompt-injected document can trigger in kitty / iTerm2
 * / WezTerm / tmux — and "ESC ] 0 ; …" retitled the window; SGR / cursor
 * sequences garbled the transcript row ("plain   31   == 31   0m"). Display
 * text is made terminal-safe: SGR colour codes dropped, every other control
 * character shown as its visible Control Picture (␛ ␇ …).
 */
import { describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { terminalSafe } from "../src/lib/terminal-safe";
import { MarkdownBlock } from "../src/ui/MarkdownBlock";
import { HighlightText } from "../src/ui/HighlightText";
import { CompanionBridgeClient } from "../src/bridge/jsonl-client";

const HOSTILE = "plain \x1b]0;TITLE\x07 \x1b]52;c;UFdOQ0xJUA==\x07 \x1b[31mred\x1b[0m \x1b[2J\x9b1m end";
const CONTROL = /[\x00-\x08\x0b-\x1f\x7f-\x9f]/;

describe("terminalSafe", () => {
  it("drops SGR, makes every other control visible, keeps text, newlines and tabs", () => {
    const safe = terminalSafe(HOSTILE);
    expect(CONTROL.test(safe)).toBe(false);
    expect(safe).toContain("␛]52;c;UFdOQ0xJUA==␇");
    expect(safe).toContain("␛]0;TITLE␇");
    expect(safe).toContain(" red ");
    expect(safe).toContain("␛[2J");
    expect(terminalSafe("a\tb\nc 日本語 🙂")).toBe("a\tb\nc 日本語 🙂");
    expect(terminalSafe("x\r\ny\rz")).toBe("x\ny\nz");
  });
});

describe("transcript renderers never paint a control character", () => {
  it("MarkdownBlock", async () => {
    const setup = await testRender(() => <MarkdownBlock content={HOSTILE} wrapWidth={100} />, { width: 110, height: 6 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    setup.renderer.destroy();
    expect(CONTROL.test(frame)).toBe(false);
    expect(frame).toContain("␛]52;c;UFdOQ0xJUA==␇");
  });

  it("HighlightText", async () => {
    const setup = await testRender(() => <HighlightText text={HOSTILE} />, { width: 110, height: 2 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    setup.renderer.destroy();
    expect(CONTROL.test(frame)).toBe(false);
  });
});

describe("bridge lines are terminal-safe before any consumer sees them", () => {
  it("an event's strings arrive with controls made visible", () => {
    const client = new CompanionBridgeClient();
    const seen: unknown[] = [];
    client.on("event", (e) => seen.push(e));
    const line = JSON.stringify({ event: { type: "text_delta", text: HOSTILE, nested: [{ out: "\x1b]52;c;QQ==\x07" }] } });
    (client as unknown as { handleLine(l: string): void }).handleLine(line);
    const text = JSON.stringify(seen[0]);
    expect(seen).toHaveLength(1);
    expect(text).not.toContain("\\u001b");
    expect(text).not.toContain("\\u0007");
    expect(text).toContain("␛]52;c;QQ==␇");
  });
});
