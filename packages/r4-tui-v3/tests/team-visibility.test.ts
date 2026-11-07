/**
 * Team visibility (2026-09-27): team-live lib units + teams-store ingest.
 * Contract: the live surface renders ONLY what the bridge really pushed or
 * the status really carried; rings are bounded; notices are one row per
 * session (upserted in place), never fabricated.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import {
  appendTeamEvent,
  clipOneLine,
  liveAgentsFromRing,
  summarizeRing,
  teamEventNoticeLine,
  teamSessionIsTerminal,
  type TeamEventLine,
} from "../src/lib/team-live";
import { teamsActions, teamsStore, setTeamsState } from "../src/stores/teams";
import { actions, resetWorkspace, workspace } from "../src/stores";

function ev(partial: Partial<TeamEventLine>): TeamEventLine {
  return { session_id: "s1", kind: "agent_status", iteration: 1, ...partial };
}

describe("team-live lib", () => {
  it("appendTeamEvent caps the ring at 200", () => {
    let ring: TeamEventLine[] = [];
    for (let i = 0; i < 210; i++) ring = appendTeamEvent(ring, ev({ iteration: i }));
    expect(ring.length).toBe(200);
    expect(ring[ring.length - 1]!.iteration).toBe(209);
  });

  it("liveAgentsFromRing: latest event wins per role", () => {
    const ring = [
      ev({ role: "pm", status: "thinking" }),
      ev({ role: "pg", status: "thinking" }),
      ev({ kind: "agent_message", role: "pm", preview: "plan drafted" }),
      ev({ role: "pm", status: "idle" }),
      ev({ role: "pg", status: "error" }),
    ];
    const agents = liveAgentsFromRing(ring);
    const pm = agents.find((a) => a.role === "pm")!;
    const pg = agents.find((a) => a.role === "pg")!;
    expect(pm.status).toBe("idle");
    expect(pm.lastPreview).toBe("plan drafted");
    expect(pg.status).toBe("error");
    expect(pg.lastPreview).toBeUndefined(); // honest absence
  });

  it("summarizeRing: empty ring stays undefined (no fabricated summary)", () => {
    expect(summarizeRing([])).toBeUndefined();
  });

  it("summarizeRing: thinking role surfaces as active with the tail line", () => {
    const summary = summarizeRing([
      ev({ role: "pm", status: "idle", emitted_at: "t0" }),
      ev({ role: "pg", status: "thinking", iteration: 3, max_iterations: 10 }),
    ])!;
    expect(summary.activeRole).toBe("pg");
    expect(summary.iteration).toBe(3);
    expect(summary.maxIterations).toBe(10);
    expect(summary.lastLine).toBe("pg thinking");
  });

  it("summarizeRing: agent_message tail clips to one line", () => {
    const summary = summarizeRing([
      ev({ kind: "agent_message", role: "se", preview: "line1\nline2 " + "x".repeat(200) }),
    ])!;
    expect(summary.lastLine).not.toContain("\n");
    expect(summary.lastLine!.length).toBeLessThanOrEqual(4 + 96);
    expect(summary.lastLine).toMatch(/^se: /);
  });

  it("clipOneLine never exceeds cap and marks truncation", () => {
    expect(clipOneLine("short")).toBe("short");
    const long = "y".repeat(500);
    expect(clipOneLine(long).endsWith("…")).toBe(true);
    expect(clipOneLine(long, 10)).toBe("yyyyyyyyy…");
  });

  it("teamSessionIsTerminal covers both cancel spellings", () => {
    expect(teamSessionIsTerminal("completed")).toBe(true);
    expect(teamSessionIsTerminal("cancelled")).toBe(true);
    expect(teamSessionIsTerminal("canceled")).toBe(true);
    expect(teamSessionIsTerminal("running")).toBe(false);
  });

  it("teamEventNoticeLine renders each kind honestly", () => {
    expect(teamEventNoticeLine(ev({ kind: "iteration_started", iteration: 2 }))).toBe(
      "i2 iteration started",
    );
    expect(teamEventNoticeLine(ev({ role: "pm", status: "thinking" }))).toBe(
      "i1 pm → thinking",
    );
    expect(
      teamEventNoticeLine(ev({ kind: "agent_message", role: "pg", preview: "" })),
    ).toBe("i1 pg: (empty)");
  });
});

describe("teams store ingest", () => {
  beforeEach(() => {
    resetWorkspace();
    setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  });

  afterEach(() => {
    resetWorkspace();
    setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  });

  it("ingestEvent appends to the session ring and updates one notice row", () => {
    teamsActions.ingestEvent(ev({ role: "pm", status: "thinking" }));
    teamsActions.ingestEvent(
      ev({ kind: "agent_message", role: "pm", preview: "plan ready" }),
    );
    const ring = teamsStore.live.s1!;
    expect(ring.length).toBe(2);
    // One upserted chat row (same key), latest tail shown.
    const notices = workspace.chatMessages.filter(
      (m) => m.role === "system" && m.noticeKey === "team:s1",
    );
    expect(notices.length).toBe(1);
    expect(notices[0]!.content).toContain("iter 1");
    expect(notices[0]!.content).toContain("pm: plan ready");
    // Details flow into the expandable slot.
    const details = workspace.noticeDetails["team:s1"]!;
    expect(details[details.length - 1]).toBe("i1 pm: plan ready");
  });

  it("ingestEvent ignores malformed lines (never crashes, never fabricates)", () => {
    teamsActions.ingestEvent({} as TeamEventLine);
    teamsActions.ingestEvent(ev({ session_id: "", role: "pm" }));
    expect(Object.keys(teamsStore.live).length).toBe(0);
  });

  it("notice details are capped at the last 8 events", () => {
    for (let i = 0; i < 12; i++) {
      teamsActions.ingestEvent(ev({ role: "pm", status: "thinking", iteration: i }));
    }
    const details = workspace.noticeDetails["team:s1"]!;
    expect(details.length).toBe(8);
    expect(details[0]).toBe("i4 pm → thinking");
  });
});

describe("async run_finished notices (2026-09-27)", () => {
  beforeEach(() => {
    resetWorkspace();
    setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  });
  afterEach(() => resetWorkspace());

  it("clean finish → continues line", () => {
    teamsActions.ingestEvent({
      session_id: "s1", kind: "run_finished", iteration: 3, continued: true, session_name: "batch-a", status: "running",
    });
    const n = workspace.chatMessages.find((m) => m.noticeKey === "team:s1");
    expect(n?.content).toContain("iteration 3 done (continues)");
  });

  it("clean finish ALSO posts the conclusion receipt (2026-09-29)", () => {
    setTeamsState("live", "s1", [
      { session_id: "s1", kind: "agent_message", iteration: 3, role: "pm", preview: "Plan: sweep three variants, keep the best ridge." },
      { session_id: "s1", kind: "agent_message", iteration: 3, role: "pg", preview: "Implemented variant B; harness green on all three." },
    ]);
    teamsActions.ingestEvent({
      session_id: "s1", kind: "run_finished", iteration: 3, session_name: "batch-a", status: "completed",
    });
    const receipt = workspace.chatMessages.find(
      (m) => m.role === "system" && m.content.includes("conclusion (iteration 3)"),
    );
    expect(receipt?.content).toContain("pm: Plan: sweep three variants");
    expect(receipt?.content).toContain("pg: Implemented variant B");
    expect(receipt?.content).toContain("full text: Work pane");
  });

  it("finish with zero agent messages says so honestly", () => {
    teamsActions.ingestEvent({
      session_id: "s1", kind: "run_finished", iteration: 1, session_name: "quiet", status: "completed",
    });
    const receipt = workspace.chatMessages.find(
      (m) => m.role === "system" && m.content.includes("conclusion (iteration 1)"),
    );
    expect(receipt?.content).toContain("no agent messages recorded");
  });

  it("long conclusions are excerpted with an honest (+N chars) tail", () => {
    setTeamsState("live", "s1", [
      { session_id: "s1", kind: "agent_message", iteration: 1, role: "pm", preview: "x".repeat(400) },
    ]);
    teamsActions.ingestEvent({
      session_id: "s1", kind: "run_finished", iteration: 1, session_name: "long", status: "completed",
    });
    const receipt = workspace.chatMessages.find(
      (m) => m.role === "system" && m.content.includes("conclusion (iteration 1)"),
    );
    expect(receipt?.content).toContain("(+160 chars)");
  });

  it("error finish keeps the tails AND posts surviving conclusions", () => {
    setTeamsState("live", "s1", [
      { session_id: "s1", kind: "agent_message", iteration: 2, role: "pg", preview: "Survivor output: variant kept." },
    ]);
    teamsActions.ingestEvent({
      session_id: "s1", kind: "run_finished", iteration: 2, session_name: "batch-a", status: "running",
      errors: [{ role: "pm", error: "API error: 503 No active endpoint" }],
    });
    const receipt = workspace.chatMessages.find(
      (m) => m.role === "system" && m.content.includes("conclusion (iteration 2)"),
    );
    expect(receipt?.content).toContain("pg: Survivor output");
  });

  it("error finish → verbatim role tails", () => {
    teamsActions.ingestEvent({
      session_id: "s1", kind: "run_finished", iteration: 2, session_name: "batch-a", status: "running",
      errors: [{ role: "pm", error: "API error: 503 No active endpoint" }],
    });
    const n = workspace.chatMessages.find((m) => m.noticeKey === "team:s1");
    expect(n?.content).toContain("1 agent error");
    expect(n?.content).toContain("503 No active endpoint");
  });
});

describe("team_started / team_start_error lanes (async bootstrap, 2026-09-28)", () => {
  beforeEach(() => {
    resetWorkspace();
    setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  });
  afterEach(() => resetWorkspace());

  it("team_started posts the bootstrap receipt", () => {
    teamsActions.ingestEvent({
      session_id: "t-1", kind: "team_started", session_name: "research-0928", crew: "research",
      iteration: 0, roles: ["pm", "pg", "se", "cd"], goal: "defect class",
      tailor: { root: "pm", tailored: ["pm", "pg"], kept_default: ["se", "cd"] },
    });
    const n = workspace.chatMessages.find((m) => m.noticeKey === "team:t-1");
    expect(n?.content).toContain("research-0928");
    expect(n?.content).toContain("pm,pg,se,cd");
  });

  it("team_started with degraded tailor names the honest fallback (2026-09-29)", () => {
    teamsActions.ingestEvent({
      session_id: "t-2", kind: "team_started", session_name: "research-x", crew: "research",
      iteration: 0, roles: ["pm"], goal: "x",
      tailor: { root: "pm", tailored: [], kept_default: ["pm"], degraded: "API error: 503: No active endpoint" },
    });
    const systems = workspace.chatMessages.filter((m) => m.role === "system");
    const degradedNotice = systems.find((m) => m.content.includes("tailoring skipped"));
    expect(degradedNotice?.content).toContain("503");
    expect(degradedNotice?.content).toContain("baseline prompts");
  });

  it("team_start_error surfaces even without a session id", () => {
    teamsActions.ingestEvent({ kind: "team_start_error", goal: "x", error_text: "API error: 503" } as never);
    const systems = workspace.chatMessages.filter((m) => m.role === "system");
    expect(systems[systems.length - 1]?.content).toContain("503");
  });
});

describe("team poller posture (2026-09-28 turn-starvation fix)", () => {
  beforeEach(() => {
    resetWorkspace();
    setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  });
  afterEach(() => resetWorkspace());

  it("quiet poll variants never write notices on failure", async () => {
    const { teamsActions } = await import("../src/stores/teams");
    // No live bridge in this test — quiet polls must fail silently, notice-free.
    const before = new Set(workspace.chatMessages.map((m) => m.content + m.timestamp));
    await teamsActions._pollStatusQuiet("no-such");
    await teamsActions._pollListQuiet();
    const after = workspace.chatMessages.filter((m) => {
      const marker = m.content + m.timestamp;
      return !before.has(marker);
    });
    expect(after.length).toBe(0);
  });
});

describe("team live poller during a chat turn (plan H-8)", () => {
  afterEach(async () => {
    const { setCompanionBridgeForTests } = await import("../src/bridge/jsonl-client");
    setCompanionBridgeForTests(null);
    setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
    resetWorkspace();
  });

  it("polls status and list while a turn streams (the bridge answers reads mid-turn)", async () => {
    const { setCompanionBridgeForTests } = await import("../src/bridge/jsonl-client");
    const { _teamsLiveTickForTests } = await import("../src/stores/teams");
    const methods: string[] = [];
    setCompanionBridgeForTests({
      alive: true,
      turnInFlight: true,
      request: async (method: string) => {
        methods.push(method);
        return method === "teamup.list" ? { sessions: [], count: 0 } : { status: "running" };
      },
    } as never);
    setTeamsState({ sessions: [{ session_id: "s1", status: "running" } as never], status: {} });
    const { setWorkspace } = await import("../src/stores");
    setWorkspace("chatStreaming", true);
    _teamsLiveTickForTests();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(methods).toEqual(["teamup.status", "teamup.list"]);
  });
});
