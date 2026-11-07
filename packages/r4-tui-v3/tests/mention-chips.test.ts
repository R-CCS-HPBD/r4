import { describe, expect, it } from "bun:test";
import { collapseChipsOnBackspace } from "../src/lib/mention-chips";

const CHIPS = ["@out/a.png"];

describe("A6 mention-chip collapse", () => {
  it("backspace inside a chip removes the whole chip + trailing space", () => {
    const prev = "open @out/a.png please";
    // deleted the 'g' of 'png'
    const next = "open @out/a.pn please";
    const result = collapseChipsOnBackspace(prev, next, CHIPS);
    expect(result.collapsed).toBe(true);
    expect(result.text).toBe("open please");
    expect(result.chips).toEqual([]);
  });

  it("backspace on the chip's first char collapses it too", () => {
    const prev = "open @out/a.png please";
    const next = "open out/a.png please";
    const result = collapseChipsOnBackspace(prev, next, CHIPS);
    expect(result.collapsed).toBe(true);
    expect(result.text).toBe("open please");
  });

  it("backspace on the space after a chip keeps the chip intact", () => {
    const prev = "open @out/a.png please";
    const next = "open @out/a.pngplease";
    const result = collapseChipsOnBackspace(prev, next, CHIPS);
    expect(result.collapsed).toBe(false);
    expect(result.text).toBe(next);
    expect(result.chips).toEqual(CHIPS);
  });

  it("backspace far from any chip passes through", () => {
    const prev = "open @out/a.png please!";
    const next = "open @out/a.png please";
    const result = collapseChipsOnBackspace(prev, next, CHIPS);
    expect(result.collapsed).toBe(false);
    expect(result.text).toBe(next);
    expect(result.chips).toEqual(CHIPS);
  });

  it("passes larger edits through and prunes chips that vanished", () => {
    const result = collapseChipsOnBackspace("open @out/a.png please", "", ["@out/a.png"]);
    expect(result.collapsed).toBe(false);
    expect(result.text).toBe("");
    expect(result.chips).toEqual(["@out/a.png"]); // pruning not applied on large edits
  });

  it("handles two chips and collapses only the edited one", () => {
    const chips = ["@a.png", "@b.pdf"];
    const prev = "@a.png mix @b.pdf now";
    // delete inside second chip (the '.' of b.pdf)
    const next = "@a.png mix @bpdf now";
    const result = collapseChipsOnBackspace(prev, next, chips);
    expect(result.collapsed).toBe(true);
    expect(result.text).toBe("@a.png mix now");
    expect(result.chips).toEqual(["@a.png"]);
  });
});
