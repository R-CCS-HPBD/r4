/**
 * Review 2026-09-25: /venue start and /venue stop adopt the journal's newer
 * copy when a remote turn finished while nobody watched it here. The bridge
 * answers `adopted: true`; the TUI must re-render the transcript from the
 * returned state (the watcher never fires again once local == journal).
 */

import { describe, expect, test, beforeEach } from "bun:test";
import { setWorkspace, workspace } from "../src/stores";
import { adoptVenueTranscript } from "../src/bridge/venue-bridge";

const adoptedState = {
  messages: [
    { role: "user", content: "long task" },
    { role: "assistant", content: "REMOTE-DONE" },
  ],
};

describe("adoptVenueTranscript", () => {
  beforeEach(() => {
    setWorkspace("chatMessages", [
      { id: "old", role: "user", content: "long task", timestamp: 0 } as never,
    ]);
  });

  test("re-renders the transcript when the bridge adopted the journal copy", () => {
    expect(adoptVenueTranscript({ adopted: true, state: adoptedState })).toBe(true);
    const contents = workspace.chatMessages.map((m) => m.content);
    expect(contents.slice(0, 2)).toEqual(["long task", "REMOTE-DONE"]);
    expect(contents[2]).toContain("adopted the server's newer copy");
  });

  test("leaves the transcript alone when nothing was adopted", () => {
    expect(adoptVenueTranscript({ adopted: false, state: adoptedState })).toBe(false);
    expect(adoptVenueTranscript({ state: adoptedState })).toBe(false);
    expect(workspace.chatMessages.map((m) => m.content)).toEqual(["long task"]);
  });
});
