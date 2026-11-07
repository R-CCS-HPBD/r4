/**
 * /term share — user-initiated relay of the terminal buffer to the agent.
 * Same relay contract as `!cmd` (sendChatMessage with relay: true), never
 * auto-streamed.
 */

import { describe, expect, test, beforeEach, afterEach, spyOn } from "bun:test";
import { resetWorkspace, actions } from "../src/stores";
import { termSession } from "../src/terminal/pty-session";
import {
  captureTermShare,
  formatTermShareForAgent,
  TERM_SHARE_MAX_CHARS,
} from "../src/terminal/share";
import { handleWaveIntercepts } from "../src/commands/wave-intercepts";

const POSIX = process.platform !== "win32";

async function waitFor(cond: () => boolean, timeoutMs = 6000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (cond()) return true;
    await new Promise((r) => setTimeout(r, 60));
  }
  return false;
}

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
  termSession.kill();
});

afterEach(() => {
  termSession.kill();
});

describe.skipIf(!POSIX)("/term share", () => {
  test("capture tails the buffer with trailing blanks trimmed", async () => {
    termSession.ensure(60, 10);
    // Marker must exist ONLY in the output, never in the command echo —
    // otherwise the wait unblocks on the echo and races the real output.
    // A one-line prompt (hermetic): a user's PS1 with a long cwd wrapped to
    // three rows at 60 cols and pushed "42" out of the 3-row tail.
    termSession.write("PS1='$ '; echo ONE; echo TWO; echo $((30+12))\n");
    expect(await waitFor(() => /^42$/m.test(termSession.captureText(50) ?? ""))).toBe(true);
    const short = captureTermShare(3);
    expect(short.kind).toBe("ok");
    if (short.kind !== "ok") return;
    // Tail-only capture: shallower than the full tail, newest content kept.
    const full = captureTermShare(50);
    expect(full.kind).toBe("ok");
    if (full.kind !== "ok") return;
    expect(short.text).toMatch(/^42$/m); // freshest output line survives
    expect(full.text).toMatch(/^ONE$/m); // deep capture reaches the oldest
    expect(short.lineCount).toBeLessThan(full.lineCount);
    // No trailing newline and no all-blank tail rows (the anchor scan drops
    // the prompt's empty viewport; interior content stays verbatim — the
    // prompt line may keep its cursor-cell space, that IS the buffer).
    expect(short.text.endsWith("\n")).toBe(false);
    const rows = short.text.split("\n");
    expect(rows.pop()?.trim()).not.toBe("");
  });

  test("no-session and empty buffer stay honest (nothing relayed)", async () => {
    expect(captureTermShare(10).kind).toBe("no-session");
    const intercepted = await handleWaveIntercepts({ name: "term", args: "share 5" });
    expect(intercepted?.kind).toBe("handled");
    const sent = spyOn(actions, "sendChatMessage");
    await handleWaveIntercepts({ name: "term", args: "share 5" });
    expect(sent).not.toHaveBeenCalled();
    sent.mockRestore();
  });

  test("character cap truncates from the front, honestly flagged", async () => {
    termSession.ensure(100, 40);
    // One huge line, longer than the char cap.
    const big = "X".repeat(TERM_SHARE_MAX_CHARS + 500);
    termSession.write(`printf '%s\\n' ${big}\n`);
    expect(await waitFor(() => (termSession.captureText(500)?.length ?? 0) > TERM_SHARE_MAX_CHARS)).toBe(true);
    const capture = captureTermShare(500);
    expect(capture.kind).toBe("ok");
    if (capture.kind !== "ok") return;
    expect(capture.truncatedChars).toBe(true);
    expect(capture.text.length).toBeLessThanOrEqual(TERM_SHARE_MAX_CHARS + 200); // the frame lines + cap
    const frame = formatTermShareForAgent(capture);
    expect(frame).toContain("truncated");
    expect(frame).toContain("terminal pane");
  });

  test("dispatch relays the frame as a relay:true turn, never a named prompt", async () => {
    termSession.ensure(60, 10);
    termSession.write("echo SHAREMARK_$((11*3))\n");
    expect(await waitFor(() => (termSession.captureText(50) ?? "").includes("SHAREMARK_33"))).toBe(true);
    const sent = spyOn(actions, "sendChatMessage").mockImplementation(() => {});
    const result = await handleWaveIntercepts({ name: "term", args: "share 20" });
    expect(result?.kind).toBe("handled");
    expect(sent).toHaveBeenCalledTimes(1);
    const [frame, opts] = sent.mock.calls[0]!;
    expect(String(frame)).toContain("SHAREMARK_33");
    expect(String(frame)).toContain("not in your tool history");
    expect(opts).toEqual({ relay: true });
    sent.mockRestore();
  });

  test("bad number arg errors without relaying", async () => {
    termSession.ensure(60, 10);
    const sent = spyOn(actions, "sendChatMessage").mockImplementation(() => {});
    const result = await handleWaveIntercepts({ name: "term", args: "share abc" });
    expect(result?.kind).toBe("error");
    expect(sent).not.toHaveBeenCalled();
    sent.mockRestore();
  });
});
