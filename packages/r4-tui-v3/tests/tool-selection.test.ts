/** ADR-037: tool-selection phase rendering — honesty by construction. */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { actions, setWorkspace, workspace } from "../src/stores";
import {
  flushToolSelectionRollup,
  onBridgeEvent,
  onToolSelection,
  toolSelectionRollupLine,
} from "../src/bridge/companion-runtime-bridge";
import { agentPhase } from "../src/components/agent-activity";
import { formatToolSchemaSuffix } from "../src/lib/cost-chip";
import { demoToolSelection } from "../src/adapters/fixtures";
import type { ToolSelectionRecord } from "../src/types";

function record(over: Partial<ToolSelectionRecord> = {}): ToolSelectionRecord {
  return {
    version: 1,
    session_id: "s",
    turn_id: "t1",
    step_index: 0,
    available_tools: 142,
    discovered: { session_registered: 142 },
    filtered_out: {},
    candidate_tools: 142,
    selected_tools: ["a"],
    selected_skills: ["code"],
    strategy: "passthrough",
    ranking: null,
    selection_latency_ms: 1,
    schema_tokens_estimate: { before: 18400, after: 18400 },
    warnings: [],
    would_filter: {},
    ...over,
  };
}

describe("toolSelectionRollupLine (honesty)", () => {
  test("passthrough+full catalog says 'no selection' verbatim", () => {
    expect(toolSelectionRollupLine(record())).toBe(
      "⚙ all 142 tools offered (no selection) · 1 skill",
    );
  });
  test("no-claim language: never says optimized/cached", () => {
    const line = toolSelectionRollupLine(record({ candidate_tools: 78, strategy: "policy_filter" }));
    expect(line).toBe("⚙ 78 of 142 tools offered (policy_filter) · 1 skill");
    expect(line).not.toMatch(/optim/i);
  });
  test("skill suffix only when skills selected", () => {
    expect(toolSelectionRollupLine(record({ selected_skills: [] }))).toBe(
      "⚙ all 142 tools offered (no selection)",
    );
    expect(toolSelectionRollupLine(record({ selected_skills: ["a", "b"] }))).toContain("2 skills");
  });
});

describe("formatToolSchemaSuffix", () => {
  test("suffix present for a real estimate, absent otherwise", () => {
    expect(formatToolSchemaSuffix(3100)).toBe(" · tools ~3.1k");
    expect(formatToolSchemaSuffix(0)).toBe("");
    expect(formatToolSchemaSuffix(undefined)).toBe("");
  });
});

describe("onToolSelection wiring", () => {
  test("store receives the record verbatim; events stream logs raw JSON", () => {
    const rec = record({ available_tools: 5, candidate_tools: 5 });
    onToolSelection(rec as unknown as Record<string, unknown>);
    expect(workspace.toolSelection?.available_tools).toBe(5);
    const last = workspace.events[workspace.events.length - 1];
    expect(last?.action).toBe("tool_selection");
    expect(JSON.parse(String(last?.detail)).available_tools).toBe(5);
  });

  test("flush writes one keyed roll-up notice with detail lines", () => {
    onToolSelection(record() as unknown as Record<string, unknown>);
    flushToolSelectionRollup();
    const notice = workspace.chatMessages.find(
      (m) => (m as { noticeKey?: string }).noticeKey === "tool-selection-summary",
    );
    expect(notice).toBeTruthy();
    expect(String((notice as { content?: string }).content)).toContain("no selection");
    const details = (workspace.noticeDetails ?? {})["tool-selection-summary"];
    expect(details?.some((d: string) => d.startsWith("schema ~"))).toBe(true);
    expect(details?.some((d: string) => d.includes("strategy passthrough"))).toBe(true);
    // the last measured record stays (chip suffix uses it between turns)
    expect(workspace.toolSelection).not.toBeNull();
  });
});

describe("amendment fields (warnings + would_filter)", () => {
  test("build emits catalog warnings when measured", () => {
    onToolSelection(
      record({
        warnings: ["default-rag-search and hatch-rag-search register the same 3 tools (double registration)"],
        would_filter: { capability_tier: { count: 3, sample: ["mcp:pg-lab.drop_table"] } },
      }) as unknown as Record<string, unknown>,
    );
    expect(workspace.toolSelection?.warnings?.[0]).toContain("default-rag-search");
    expect(workspace.toolSelection?.would_filter?.capability_tier?.count).toBe(3);
    flushToolSelectionRollup();
    const details = (workspace.noticeDetails ?? {})["tool-selection-summary"];
    expect(details?.some((d: string) => d.startsWith("⚠ "))).toBe(true);
    expect(details?.some((d: string) => d.startsWith("would filter capability_tier"))).toBe(true);
  });

  test("empty bucket stays silent (no fabricated disagreement size)", () => {
    onToolSelection(record() as unknown as Record<string, unknown>);
    flushToolSelectionRollup();
    const details = (workspace.noticeDetails ?? {})["tool-selection-summary"];
    expect(details?.some((d: string) => d.startsWith("would filter"))).toBe(false);
  });
});

describe("selection phase label lifecycle (user report 2026-10-06)", () => {
  beforeEach(() => {
    setWorkspace("chatStreaming", true);
    setWorkspace("chatTurnStatus", null);
  });
  afterEach(() => {
    setWorkspace("chatStreaming", false);
    setWorkspace("chatTurnStatus", null);
  });

  test("tool_selection sets the live label only while a turn streams", () => {
    onToolSelection(record() as unknown as Record<string, unknown>);
    expect(workspace.chatTurnStatus).toBe("selecting tools");
    expect(agentPhase()).toBe("running");
  });

  test("response_start clears the label — the phase never outlives dispatch", () => {
    // The stuck-state repro: label pinned for the whole thinking stream.
    onToolSelection(record() as unknown as Record<string, unknown>);
    expect(workspace.chatTurnStatus).toBe("selecting tools");
    onBridgeEvent({ type: "response_start" });
    expect(workspace.chatTurnStatus).toBeNull();
    // the status line falls back to the honest thinking phase from here
    expect(agentPhase()).toBe("thinking");
  });

  test("a stale tool label from the previous round also drops at response_start", () => {
    setWorkspace("chatTurnStatus", "🔧 shell_exec: ls");
    onBridgeEvent({ type: "response_start" });
    expect(workspace.chatTurnStatus).toBeNull();
  });

  test("idle injection never claims a phase (no streaming turn)", () => {
    setWorkspace("chatStreaming", false);
    onToolSelection(record() as unknown as Record<string, unknown>);
    expect(workspace.chatTurnStatus).toBeNull();
  });
});

describe("demo fixture honesty", () => {
  test("fixture parses through the same handler and is labeled", () => {
    onToolSelection(demoToolSelection.payload as unknown as Record<string, unknown>);
    expect(workspace.toolSelection?.available_tools).toBe(142);
    expect(workspace.toolSelection?.strategy).toBe("passthrough");
    const last = workspace.events[workspace.events.length - 1];
    expect(last?.action).toBe("tool_selection");
  });
});
