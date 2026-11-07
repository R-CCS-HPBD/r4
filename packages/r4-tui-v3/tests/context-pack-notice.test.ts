/**
 * ADR-093 D-8: the harness pre-retrieval announcement ("Retrieved n passages
 * (status)") reaches chat. The Python bridge forwards THINKING events of kind
 * context_pack only (review r4-harness-9, 2026-10-10); before this branch the
 * TUI received them and drew nothing.
 */

import { beforeEach, describe, expect, test } from "bun:test";
import {
  onBridgeEvent,
  resetCompanionRuntimeBridgeForTests,
} from "../src/bridge/companion-runtime-bridge";
import { resetWorkspace, workspace } from "../src/stores";

beforeEach(() => {
  resetWorkspace();
  resetCompanionRuntimeBridgeForTests();
});

function systemLines(): string[] {
  return workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);
}

describe("context pack announcement", () => {
  test("the retrieval line is posted to chat", () => {
    onBridgeEvent({
      type: "thinking",
      message: "Retrieved 3 passages (sufficient)",
      data: { kind: "context_pack", sids: ["S1", "S2", "S3"] },
    });
    const lines = systemLines();
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain("Retrieved 3 passages (sufficient)");
  });

  test("the unknown model-scale notice is posted to chat", () => {
    // ADR-093 D-14 (C22, wave D 2026-10-10): once per session.
    onBridgeEvent({
      type: "thinking",
      message: "model scale unknown for house@lab; set it with `r4 model scale set house small|medium|large`",
      data: { kind: "model_scale", size_class: "unknown", profile: "medium" },
    });
    const lines = systemLines();
    expect(lines).toHaveLength(1);
    expect(lines[0]).toContain("model scale unknown for house@lab");
  });

  test("other thinking events stay out of chat", () => {
    onBridgeEvent({ type: "thinking", message: "Processing your message…", data: {} });
    onBridgeEvent({ type: "thinking", message: "", data: { kind: "context_pack" } });
    expect(systemLines()).toHaveLength(0);
  });
});
