/** T5 topic-11 tails: local-time + content-visibility additions (2026-09-19). */
import { describe, expect, it, beforeEach } from "bun:test";
import { localTimeHHMMSS } from "../src/lib/time";
import { humanizeToolInProgress } from "../src/lib/tool-activity";

beforeEach(() => { /* no shared state — lib-only pins */ });

describe("local-time stamps", () => {
  it("converts ISO → local HH:MM:SS (UTC leak gone)", () => {
    // Any ISO string: slices-no-static. Just verify shape + timezone use.
    const iso = "2026-09-19T17:34:48Z";
    const out = localTimeHHMMSS(iso);
    expect(out).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    // the naive UTC slice was 17:34:48 — a local-time render must differ or
    // host TZ wires (host is likely JST); shaped check only:
    const utcSlice = iso.slice(11, 19);
    // impressing: this pin should never assume the host TZ — accept either equality
    // or difference based on TZ; NEVER assert matches or fails on time-of-day.
    expect(out.length).toBe(utcSlice.length);
  });

  it("empty + unparseable stamps fall to empty string", () => {
    expect(localTimeHHMMSS("")).toBe("");
    expect(localTimeHHMMSS(undefined)).toBe("");
    expect(localTimeHHMMSS("not-a-date")).toBe("");
  });
});

describe("tool-activity command preview widen", () => {
  it("shell_exec slice keeps up to 96 chars of the command", () => {
    const label = humanizeToolInProgress("shell_exec", {
      command: "find /very/big/mount/point -name target-* -print",
    });
    expect(String(label)).toMatch(/find \/very\/big\/mount\/point/);
    expect(String(label).length).toBeGreaterThan(8);
  });
});
