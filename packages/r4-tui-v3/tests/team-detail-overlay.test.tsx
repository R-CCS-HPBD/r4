/**
 * Team-detail overlay render pins (2026-09-27): the roster renders real
 * status rows, and clicking an agent shows its verbatim prompt with the
 * source label. Absent prompt → honest placeholder, never invented text.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { TeamDetailOverlay } from "../src/components/team-detail-overlay";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setTeamsState, type TeamSessionStatus } from "../src/stores/teams";

function statusWithAgents(agents: Record<string, Record<string, unknown>>): TeamSessionStatus {
  return {
    session_id: "s1",
    name: "defect-team",
    status: "running",
    iteration: 2,
    max_iterations: 10,
    agents: agents as never,
  };
}

beforeEach(() => {
  resetWorkspace();
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 120));
  actions.setTeamDetailId(null);
  resetWorkspace();
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
});

describe("TeamDetailOverlay", () => {
  it("renders the roster with live status overrides", async () => {
    actions.setTeamDetailId("s1");
    setTeamsState("status", "s1", statusWithAgents({
      pm: { role: "pm", status: "idle", messages_sent: 3 },
      pg: { role: "pg", status: "waiting", messages_sent: 1 },
    }));
    setTeamsState("live", "s1", [
      { session_id: "s1", kind: "agent_status", iteration: 2, role: "pg", status: "thinking" },
    ]);

    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("defect-team");
    expect(frame).toContain("iter 2/10");
    expect(frame).toContain("pm · idle");
    expect(frame).toContain("pg · thinking");
    expect(frame).not.toContain("pg · waiting"); // live ring wins the display
    setup.renderer.destroy();
  });

  /** Find the rendered row index holding `needle` (geometry-robust clicks). */
  function frameRowOf(frame: string, needle: string): number {
    const idx = frame.split("\n").findIndex((line) => line.includes(needle));
    expect(idx).toBeGreaterThanOrEqual(0);
    return idx;
  }

  it("click supervisor: agent row expansion shows the verbatim prompt + source", async () => {
    actions.setTeamDetailId("s1");
    setTeamsState("status", "s1", statusWithAgents({
      pm: {
        role: "pm",
        status: "idle",
        prompt: "You plan the experiment.\nNever fabricate results.",
        prompt_source: "override",
        model: "plan-model-1",
      },
    }));

    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    // Prompt hidden before any selection.
    expect(setup.captureCharFrame()).not.toContain("You plan the experiment.");
    const y = frameRowOf(setup.captureCharFrame(), "pm · idle");
    await setup.mockMouse.click(40, y);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("You plan the experiment.");
    expect(frame).toContain("Never fabricate results.");
    expect(frame).toContain("(spec override)");
    setup.renderer.destroy();
  });

  it("agent without prompt shows the honest placeholder, never invented text", async () => {
    actions.setTeamDetailId("s1");
    setTeamsState("status", "s1", statusWithAgents({
      pm: { role: "pm", status: "idle" },
    }));

    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    const y = frameRowOf(setup.captureCharFrame(), "pm · idle");
    await setup.mockMouse.click(40, y);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("prompt — not carried by this bridge");
    setup.renderer.destroy();
  });

  it("expanded agent shows the FULL final answer + unclipped work rows (2026-09-29)", async () => {
    actions.setTeamDetailId("s1");
    setTeamsState("status", "s1", statusWithAgents({
      pm: { role: "pm", status: "idle", messages_sent: 2 },
    }));
    const longAnswer = "CONCLUSION-ALPHA " + "y".repeat(120) + " CONCLUSION-OMEGA";
    const longWork = "WORK-START " + "z".repeat(140) + " WORK-END";
    setTeamsState("live", "s1", [
      { session_id: "s1", kind: "agent_message", iteration: 2, role: "pm", preview: longAnswer },
      {
        session_id: "s1", kind: "agent_activity", iteration: 2, role: "pm",
        activity_kind: "tool_result", name: "file_read", preview: longWork,
      },
    ]);

    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 40 });
    await setup.renderOnce();
    // Nothing of the final answer is visible before expansion.
    expect(setup.captureCharFrame()).not.toContain("final answer");
    const y = frameRowOf(setup.captureCharFrame(), "pm · idle");
    await setup.mockMouse.click(40, y);
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    // Both ends of the wrapped final answer survive (nothing silently cut).
    expect(frame).toContain("final answer — pm");
    expect(frame).toContain("CONCLUSION-ALPHA");
    expect(frame).toContain("CONCLUSION-OMEGA");
    // The work row shows its whole preview too (was a hard 84-char clip).
    expect(frame).toContain("WORK-START");
    expect(frame).toContain("WORK-END");
    setup.renderer.destroy();
  });

  it("expanded agent with no final message shows no invented final panel", async () => {
    actions.setTeamDetailId("s1");
    setTeamsState("status", "s1", statusWithAgents({
      pm: { role: "pm", status: "idle" },
    }));
    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    const y = frameRowOf(setup.captureCharFrame(), "pm · idle");
    await setup.mockMouse.click(40, y);
    await setup.renderOnce();
    expect(setup.captureCharFrame()).not.toContain("final answer");
    setup.renderer.destroy();
  });

  it("overlay stays closed when no team is selected", async () => {
    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).not.toContain("teamup session");
    setup.renderer.destroy();
  });
});

describe("TeamDetailOverlay keyboard (2026-09-27)", () => {
  it("store-driven cursor + expansion (keymap verbs): pg's prompt shows", async () => {
    actions.setTeamDetailId("s1");
    setTeamsState("status", "s1", statusWithAgents({
      pm: { role: "pm", status: "idle", prompt: "PM prompt." },
      pg: { role: "pg", status: "thinking", prompt: "PG prompt." },
    }));

    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    // The global keymap verbs write these; the overlay is a passive render.
    actions.teamOverlayMoveCursor(1);
    actions.teamOverlayToggleExpanded("pg");
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("PG prompt.");
    expect(frame).not.toContain("PM prompt.");
    setup.renderer.destroy();
  });

  it("Esc sweep closes the overlay (keymap layer, B4)", async () => {
    actions.setTeamDetailId("s1");
    setTeamsState("status", "s1", statusWithAgents({
      pm: { role: "pm", status: "idle" },
    }));
    const setup = await testRender(() => <TeamDetailOverlay />, { width: 90, height: 30 });
    await setup.renderOnce();
    expect(setup.captureCharFrame()).toContain("teamup session");
    const { overlayCloseSweep } = await import("../src/keymap");
    overlayCloseSweep();
    await setup.renderOnce();
    expect(workspace.teamDetailId).toBeNull();
    setup.renderer.destroy();
  });
});
