/** T5 topic-16 pins: prose tint theming + emphasis bold inline coloring. */
import { describe, expect, it, beforeAll } from "bun:test";
import { palette, applyTheme, listThemes } from "../src/themes";
import { highlightLine } from "../src/lib/text-highlight";

beforeAll(() => {
  applyTheme("aurora", { persist: false, env: {} });
});

describe("theme palette — prose-tint keys", () => {
  it("every tracked theme carries distinct proseAssistant/proseSystem colors", () => {
    for (const theme of listThemes()) {
      applyTheme(theme.name, { persist: false, env: {} });
      expect(palette.proseAssistant).toBeTruthy();
      expect(palette.proseSystem).toBeTruthy();
      expect(palette.proseAssistant.startsWith("#")).toBe(true);
      expect(palette.proseSystem.startsWith("#")).toBe(true);
    }
    applyTheme("aurora", { persist: false, env: {} });
  });

  it("unknown-theme fallback lands on aurora's pair", () => {
    applyTheme("aurora", { persist: false, env: {} });
    const expected = palette.proseAssistant;
    expect(expected).toBeTruthy();
  });
});

describe("text-highlight — bold + italic inline-emphasis", () => {
  it("bold tokens hook the mdHeading slot of the active theme", () => {
    applyTheme("aurora", { persist: false, env: {} });
    const spans = highlightLine("started **Bold Action** right now");
    const bold = spans.find((s) => s.text.includes("**"));
    expect(bold).toBeDefined();
    expect(bold!.fg).toBe(palette.mdHeading);
  });

  it("underscore-italics land on fgDim (still dimmer than active proseAssistant)", () => {
    applyTheme("aurora", { persist: false, env: {} });
    const spans = highlightLine("note the __light emphasis__ phrase");
    const it = spans.find((s) => s.text.includes("__"));
    expect(it).toBeDefined();
    expect(it!.fg).toBe(palette.fgDim);
  });

  it("backtick code stays slang-colored the same as before", () => {
    applyTheme("aurora", { persist: false, env: {} });
    const spans = highlightLine("run `yarn dev` once");
    const tick = spans.find((s) => s.text.includes("`"));
    expect(tick).toBeDefined();
    expect(tick!.fg).toBe(palette.mdCode);
  });
});
