import { describe, expect, it } from "bun:test";
import { clampIndex, moveListIndex, handleListKey } from "../src/lib/selectable-list";

describe("selectable-list", () => {
  it("clamps and moves within bounds", () => {
    expect(clampIndex(5, 3)).toBe(2);
    expect(moveListIndex(0, 3, "down")).toBe(1);
    expect(moveListIndex(2, 3, "down")).toBe(2);
    expect(moveListIndex(0, 3, "up")).toBe(0);
  });

  it("handleListKey moves and enters", () => {
    let idx = 0;
    let entered = -1;
    expect(
      handleListKey("down", {
        selected: 0,
        length: 3,
        onMove: (i) => {
          idx = i;
        },
      }),
    ).toBe(true);
    expect(idx).toBe(1);
    expect(
      handleListKey("enter", {
        selected: 1,
        length: 3,
        onMove: () => {},
        onEnter: (i) => {
          entered = i;
        },
      }),
    ).toBe(true);
    expect(entered).toBe(1);
  });
});
