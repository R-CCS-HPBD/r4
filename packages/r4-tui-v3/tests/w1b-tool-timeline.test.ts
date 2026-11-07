import { describe, expect, test } from "bun:test";
import {
  formatDuration,
  timelineStatusIcon,
  toolActionIcon,
  truncate,
} from "../src/lib/tool-timeline";

describe("tool-timeline helpers", () => {
  test("truncate", () => {
    expect(truncate("short", 40)).toBe("short");
    expect(truncate("x".repeat(50), 10)).toBe("xxxxxxxxx…");
    expect(truncate(null)).toBe("");
  });

  test("timelineStatusIcon", () => {
    expect(timelineStatusIcon("success")).toBe("✓");
    expect(timelineStatusIcon("blocked")).toBe("⊘");
    expect(timelineStatusIcon("error")).toBe("✗");
    expect(timelineStatusIcon("errored")).toBe("✗");
    expect(timelineStatusIcon("whatever")).toBe("→");
  });

  test("formatDuration", () => {
    expect(formatDuration(null)).toBe("");
    expect(formatDuration(0)).toBe("");
    expect(formatDuration(420)).toBe("420ms");
    expect(formatDuration(1500)).toBe("1.5s");
  });

  test("toolActionIcon", () => {
    expect(toolActionIcon("tool_call")).toBe("◐");
    expect(toolActionIcon("tool_result")).toBe("✓");
    expect(toolActionIcon("tool_result", "⚠ ⛔ blocked")).toBe("⊘");
    expect(toolActionIcon("other")).toBe("•");
  });
});
