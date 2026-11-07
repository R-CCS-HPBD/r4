import { expect, test } from "bun:test";

import {
  CTRL_C_WINDOW_MS,
  ctrlCAction,
  exitChord,
  isDoubleEsc,
  joinQueued,
  reverseSearch,
  rewindCandidates,
  stashToggle,
} from "../src/lib/tui-parity";

test("Ctrl+C interrupts a turn, else clears a draft, else arms the exit chord", () => {
  expect(ctrlCAction({ streaming: true, draft: "x" })).toBe("interrupt");
  expect(ctrlCAction({ streaming: false, draft: "half typed" })).toBe("clear");
  expect(ctrlCAction({ streaming: false, draft: "  " })).toBe("exit-chord");
});

test("exit chord needs a second press inside the window", () => {
  const first = exitChord(null, 1000, CTRL_C_WINDOW_MS);
  expect(first).toEqual({ exit: false, armedAt: 1000 });
  expect(exitChord(first.armedAt, 2500, CTRL_C_WINDOW_MS).exit).toBe(true);
  expect(exitChord(first.armedAt, 4000, CTRL_C_WINDOW_MS)).toEqual({ exit: false, armedAt: 4000 });
});

test("double Esc window", () => {
  expect(isDoubleEsc(null, 100)).toBe(false);
  expect(isDoubleEsc(100, 500)).toBe(true);
  expect(isDoubleEsc(100, 900)).toBe(false);
});

test("reverse search walks older matches, case-insensitively", () => {
  const hist = ["run loop", "show Graph", "export bundle", "graph query"];
  expect(reverseSearch(hist, "graph", hist.length)).toEqual({ index: 3, text: "graph query" });
  expect(reverseSearch(hist, "graph", 3)).toEqual({ index: 1, text: "show Graph" });
  expect(reverseSearch(hist, "graph", 1)).toBeNull();
  expect(reverseSearch(hist, "", hist.length)?.index).toBe(3);
});

test("stash toggles a draft out and back", () => {
  const stashed = stashToggle("my long prompt", []);
  expect(stashed.draft).toBe("");
  expect(stashed.stash).toEqual(["my long prompt"]);
  const restored = stashToggle("", stashed.stash);
  expect(restored).toMatchObject({ draft: "my long prompt", stash: [] });
  expect(stashToggle("", []).notice).toBe("nothing stashed");
});

test("Ctrl+S over an existing stash keeps both — restores newest first (composer#6)", () => {
  const first = stashToggle("first important draft", []);
  const second = stashToggle("second draft", first.stash);
  expect(second.notice).toContain("2 stashed");
  const r1 = stashToggle("", second.stash);
  expect(r1.draft).toBe("second draft");
  const r2 = stashToggle("", r1.stash);
  expect(r2.draft).toBe("first important draft");
  expect(stashToggle("", r2.stash).notice).toBe("nothing stashed");
});

test("rewind candidates are user prompts, newest first, queued excluded", () => {
  const msgs = [
    { role: "user", content: "first question" },
    { role: "assistant", content: "answer" },
    { role: "user", content: "second\nline" },
    { role: "user", content: "queued one", queued: true },
  ];
  expect(rewindCandidates(msgs)).toEqual([
    { index: 2, preview: "second line" },
    { index: 0, preview: "first question" },
  ]);
});

test("queued prompts join as one turn", () => {
  expect(joinQueued([" a ", "", "b"])).toBe("a\n\nb");
});
