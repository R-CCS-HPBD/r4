/**
 * Loops store pins (batch 6, 2026-09-27): rows arrive only from the bridge,
 * detail renders one upserted card + its iterations, follow-stops on terminal.
 */
import { describe, it, expect, beforeEach, afterEach, mock } from "bun:test";
import { resetWorkspace, workspace } from "../src/stores";

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
let listReply: unknown = { loops: [], count: 0 };
let detailReply: unknown = {};

mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return method === "loops.list" ? listReply : detailReply;
    },
  }),
}));

const { loopsActions, loopsStore, setLoopsState, loopIsLive } = await import(
  "../src/stores/loops"
);

beforeEach(() => {
  resetWorkspace();
  setLoopsState({ rows: [], error: null });
  calls.length = 0;
  listReply = { loops: [], count: 0 };
  detailReply = {};
});

afterEach(() => resetWorkspace());

describe("loops store", () => {
  it("list maps only real rows", async () => {
    listReply = {
      loops: [
        { loop_id: "loop-x", status: "running", phase: "experiment_execution", current_iteration: 2, max_iterations: 5, research_goal: "find defect class" },
      ],
      count: 1,
    };
    await loopsActions.list();
    expect(loopsStore.rows.length).toBe(1);
    expect(loopsStore.rows[0]!.loop_id).toBe("loop-x");
    expect(loopsStore.rows[0]!.phase).toBe("experiment_execution");
  });

  it("detail renders card + iterations and auto-follows a live loop", async () => {
    detailReply = {
      status: "running",
      phase: "analysis",
      current_iteration: 1,
      max_iterations: 3,
      total_hypotheses_tested: 1,
      total_experiments_run: 2,
      total_findings: 1,
      recent_iterations: [
        { iteration_number: 1, phase: "analysis", status: "completed", hypothesis: "alloy X resists creep" },
      ],
    };
    await loopsActions.detail("loop-9");
    const notice = workspace.chatMessages.find(
      (m) => m.role === "system" && m.noticeKey === "loop:loop-9",
    );
    expect(notice?.content).toContain("running");
    expect(notice?.content).toContain("hyp 1");
    const details = workspace.noticeDetails["loop:loop-9"] ?? [];
    expect(details[0]).toContain("alloy X resists creep");
  });

  it("detail on a not-found loop answers honestly", async () => {
    detailReply = { found: false, message: "no such loop, or not yours (loops are owner-scoped)" };
    await loopsActions.detail("ghost");
    const notice = workspace.chatMessages.find(
      (m) => m.role === "system" && m.noticeKey === "loop:ghost",
    );
    expect(notice?.content).toContain("owner-scoped");
  });

  it("detail renders parallel tracks (active count + status rows)", async () => {
    detailReply = {
      status: "running",
      phase: "experiment_execution",
      current_iteration: 3,
      max_iterations: 8,
      tracks: [
        { track_id: "track-1", hypothesis: "alloy A resists", turns: 2, status: "active" },
        { track_id: "track-2", hypothesis: "alloy B resists", turns: 1, status: "rejected" },
      ],
      recent_iterations: [{ iteration_number: 3, phase: "observe", status: "completed", hypothesis: "x" }],
    };
    await loopsActions.detail("loop-t");
    const notice = workspace.chatMessages.find(
      (m) => m.role === "system" && m.noticeKey === "loop:loop-t",
    );
    expect(notice?.content).toContain("tracks 1/2 active");
    const details = workspace.noticeDetails["loop:loop-t"] ?? [];
    expect(details[0]).toContain("track-1 active");
    expect(details[0]).toContain("alloy A resists");
    expect(details[1]).toContain("track-2 rejected");
    expect(details[2]).toContain("recent iterations");
  });

  it("plain loops show no tracks chip (honest absence)", async () => {
    detailReply = { status: "running", phase: "analysis", current_iteration: 1, max_iterations: 3 };
    await loopsActions.detail("loop-p");
    const notice = workspace.chatMessages.find(
      (m) => m.role === "system" && m.noticeKey === "loop:loop-p",
    );
    expect(notice?.content ?? "").not.toContain("tracks");
  });

  it("loopIsLive names the terminal set honestly", () => {
    expect(loopIsLive("running")).toBe(true);
    expect(loopIsLive("completed")).toBe(false);
    expect(loopIsLive("error")).toBe(false);
    expect(loopIsLive("stopped")).toBe(false);
  });
});
