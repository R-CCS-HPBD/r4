/**
 * /tour wizard contract: steps reference real commands, Enter hands command
 * to the composer and advances, n/b navigate, Esc closes, args route.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { TourWizardOverlay } from "../src/components/tour-wizard";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { TOUR_STEPS, tourProgressBar } from "../src/lib/tour-steps";
import { handleTourCommand } from "../src/commands/tour-command";
import { PROJECT_SUBCOMMANDS } from "../src/commands/slash";

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("tour step data", () => {
  it("every verb used in step commands exists in the project subcommand table", () => {
    const known = new Set(PROJECT_SUBCOMMANDS.map((s) => s.name));
    for (const step of TOUR_STEPS) {
      const m = /^\/project (\w+)/.exec(step.command.trim());
      if (!m) continue;
      expect(known.has(m[1]!), `step ${step.id} uses /project ${m[1]}`).toBe(true);
    }
  });

  it("progress bar marks behind/current/ahead", () => {
    expect(tourProgressBar(0)).toBe(`▶${"□".repeat(TOUR_STEPS.length - 1)}`);
    expect(tourProgressBar(1).startsWith("■▶")).toBe(true);
    expect(tourProgressBar(TOUR_STEPS.length - 1)).toBe(`${"■".repeat(TOUR_STEPS.length - 1)}▶`);
  });
});

describe("tour command routing", () => {
  it("bare /tour opens the wizard at 0", () => {
    const result = handleTourCommand("");
    expect(result.kind).toBe("handled");
    expect(workspace.tourOpen).toBe(true);
    expect(workspace.tourStep).toBe(0);
  });

  it("/tour 3 jumps to step 2 (1-based)", () => {
    const result = handleTourCommand("3");
    expect(result.kind).toBe("handled");
    expect(workspace.tourOpen).toBe(true);
    expect(workspace.tourStep).toBe(2);
  });

  it("/tour map prints the function index instead of opening the wizard", async () => {
    const result = handleTourCommand("map");
    expect(result.kind).toBe("handled");
    await new Promise((r) => setTimeout(r, 50));
    expect(workspace.tourOpen).toBe(false);
    const notice = String(workspace.chatMessages.at(-1)?.content ?? "");
    expect(notice).toContain("chat with registered endpoints");
  });
});

describe("wizard overlay", () => {
  it("Enter on a command step prefills composer-equivalent draft and advances", async () => {
    actions.openTour(1); // create-project step
    const setup = await testRender(() => <TourWizardOverlay />, { width: 120, height: 30 });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("step 2 of 10");
    expect(frame).toContain("/project create demo");

    setup.mockInput.pressEnter();
    await setup.renderOnce();
    expect(workspace.tourOpen).toBe(false);
    expect(workspace.tourStep).toBe(2);
    expect(workspace.pendingChatDraft).toBe("/project create demo");
    setup.renderer.destroy();
  });

  it("n advances, b goes back, Esc closes", async () => {
    actions.openTour(0);
    const setup = await testRender(() => <TourWizardOverlay />, { width: 120, height: 30 });
    await setup.renderOnce();
    setup.mockInput.pressKey("n" as never);
    await setup.renderOnce();
    expect(workspace.tourStep).toBe(1);
    setup.mockInput.pressKey("b" as never);
    await setup.renderOnce();
    expect(workspace.tourStep).toBe(0);
    setup.mockInput.pressEscape();
    await setup.renderOnce();
    await new Promise((r) => setTimeout(r, 200));
    await setup.renderOnce();
    expect(workspace.tourOpen).toBe(false);
    setup.renderer.destroy();
  });
});
