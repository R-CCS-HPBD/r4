/** T5 topic-13 tail pins: diff-shape detection + kind→fg mapping (chatter rule). */
import { describe, expect, it } from "bun:test";
import { blockIsDiffLike } from "../src/components/chat";
import { humanizeToolInProgress } from "../src/lib/tool-activity";

describe("blockIsDiffLike", () => {
  it("detects a real unified diff block", () => {
    const body = [
      "--- a/foo.py",
      "+++ b/foo.py",
      "@@ -1,2 +1,2 @@",
      "-old line",
      "+new line",
      " context",
    ].join("\n");
    expect(blockIsDiffLike(body)).toBe(true);
  });

  it("does not treat prose as diff even with +/- markers", () => {
    const prose = [
      "- this is a plain explanation bullet",
      "+ that is another one",
    ].join("\n");
    expect(blockIsDiffLike(prose)).toBe(false);
  });

  it("does not color when no structure rows present (hunk/meta only)", () => {
    const additionsOnly = [
      "+foo",
      "bar context",
    ].join("\n");
    expect(blockIsDiffLike(additionsOnly)).toBe(false);
  });
});

describe("work chip pattern stays", () => {
  it("command preview preserves up to 96 chars for visibility", () => {
    const cmd = "find /very/long/root/level/mount/point/with/deep/nesting -name \"*.py\" -type f -print";
    const label = humanizeToolInProgress("shell_exec", { command: cmd });
    expect(String(label).length).toBeGreaterThan(20);
    expect(String(label)).toMatch(/^Running find/);
  });
});
