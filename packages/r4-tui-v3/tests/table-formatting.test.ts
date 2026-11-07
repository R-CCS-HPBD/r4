import { describe, expect, test } from "bun:test";
import { fitCell, splitTableSegments, tableColumnWidths } from "../src/lib/markdown-tables";

describe("GFM table extraction", () => {
  test("header + separator + rows becomes a table segment", () => {
    const segments = splitTableSegments([
      "| Name | Status |",
      "| --- | --- |",
      "| umrs | ready |",
      "| code | ready |",
    ]);
    expect(segments).toHaveLength(1);
    const segment = segments[0]!;
    expect(segment.kind).toBe("table");
    if (segment.kind === "table") {
      expect(segment.table.header).toEqual(["Name", "Status"]);
      expect(segment.table.rows).toEqual([
        ["umrs", "ready"],
        ["code", "ready"],
      ]);
    }
  });

  test("plain-paragraph pipes do NOT become tables (conservative)", () => {
    const segments = splitTableSegments(["Uses of | a or b | symbol.", "plain line after"]);
    expect(segments.every((s) => s.kind === "text")).toBe(true);
  });

  test("mixed text and table keeps ordering", () => {
    const segments = splitTableSegments([
      "before",
      "| A | B |",
      "|---|---|",
      "| 1 | 2 |",
      "after",
    ]);
    expect(segments).toHaveLength(3);
    expect(segments[0]!.kind).toBe("text");
    expect(segments[1]!.kind).toBe("table");
    expect(segments[2]!.kind).toBe("text");
  });

  test("leading/trailing border pipes are optional", () => {
    const segments = splitTableSegments([
      "Name | Status",
      "--- | ---: ",
      "one | two",
    ]);
    expect(segments[0]!.kind).toBe("table");
  });
});

describe("column widths and cell fitting", () => {
  test("column width is max content length capped at 28", () => {
    const table = {
      header: ["short"],
      rows: [["this is a much longer cell value that exceeds"]],
    };
    expect(tableColumnWidths(table)[0]).toBe(28);
  });

  test("fitCell pads or ellipsizes", () => {
    expect(fitCell("abc", 5)).toBe("abc  ");
    expect(fitCell("abcdefgh", 4)).toBe("abc…");
    expect(fitCell("ab", 2)).toBe("ab");
  });
});
