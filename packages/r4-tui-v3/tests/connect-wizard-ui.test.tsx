/**
 * Connect wizard UI regression pins (field report 2026-10-20).
 *
 * From another PC every /connect attempt failed while the CLI worked: the
 * wizard silently capped every answer at 512 chars, so a pasted JWT longer
 * than that was stored truncated (login still said "ok" — only re-login
 * from the CLI, which has no cap, repaired it), and a credentialed http://
 * URL had no path to r4's G-4 opt-in (`--allow-insecure-http`).
 *
 * These pins assert a long pasted token stays whole with its length shown,
 * that over-long input is REFUSED loudly (never capped), and that the G-4
 * opt-in question appears exactly once url+key say credentialed plain http.
 */

import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { resetWorkspace, actions } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { agentActions } from "../src/stores/agent-runtime";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(async () => {
  await new Promise((resolve) => setTimeout(resolve, 200));
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  actions.closeAllOverlays();
});

async function renderWizard() {
  const setup = await testRender(() => <App forceOffline={true} />, {
    width: 160,
    height: 60,
  });
  await setup.renderOnce();
  actions.closeAllOverlays();
  await setup.renderOnce();
  return setup;
}

describe("ConnectWizardOverlay UI", () => {
  it("keeps a >512-char pasted token whole and shows its length", async () => {
    const setup = await renderWizard();
    actions.openConnectWizard("login");
    await setup.renderOnce();

    // 906 chars — nearly twice the old silent 512 cap. Repeat-built: the
    // ci-lint §0 JWT regex must never see a fixture-shaped token.
    const token = `h.${"p".repeat(900)}.sig`;
    await setup.mockInput.pasteBracketedText(token);
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).toContain("(906 chars)");
    expect(frame).not.toContain("too long");

    setup.renderer.destroy();
  });

  it("refuses input past the cap loudly instead of capping silently", async () => {
    const setup = await renderWizard();
    actions.openConnectWizard("login");
    await setup.renderOnce();

    await setup.mockInput.pasteBracketedText("x".repeat(5000));
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).toContain("too long");
    expect(frame).not.toContain("(5000 chars)");

    setup.renderer.destroy();
  });

  it("adds the G-4 opt-in question once url+key say credentialed plain http", async () => {
    const setup = await renderWizard();
    actions.openConnectWizard("source");
    await setup.renderOnce();

    await setup.mockInput.typeText("rikyu");
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    await setup.mockInput.typeText("http://10.0.0.1:8000/v1");
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    await setup.mockInput.typeText("key-with-dashes-123");
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    // provider + description stay empty (two Enters), then the G-4 question.
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).toContain("type yes to allow");
    expect(frame).toContain("6/6");

    setup.renderer.destroy();
  });

  it("an https url keeps the flow at five questions (no G-4 question)", async () => {
    const setup = await renderWizard();
    actions.openConnectWizard("source");
    await setup.renderOnce();

    await setup.mockInput.typeText("rikyu");
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    await setup.mockInput.typeText("https://rikyu.example/v1");
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    await setup.mockInput.typeText("key-with-dashes-123");
    setup.mockInput.pressEnter();
    await setup.renderOnce();
    // provider stays empty; description shows as the LAST question — do not
    // press Enter on it (that Enter would dispatch the line).
    setup.mockInput.pressEnter();
    await setup.renderOnce();

    const frame = setup.captureCharFrame();
    expect(frame).toContain("5/5");
    expect(frame).not.toContain("type yes to allow");

    setup.renderer.destroy();
  });
});

/**
 * Field report 2026-10-21: "Add public endpoint" never registered anything.
 * submitStep closed the wizard BEFORE building the line, so flow() read its
 * "login" fallback and the run dispatched `/login <source name>` — the
 * stored login was overwritten with the name. The pins above stop short of
 * the final Enter; these press it and read the line that is dispatched.
 */
describe("ConnectWizardOverlay final Enter dispatches its own flow's line", () => {
  async function answerAll(answers: string[]): Promise<string[]> {
    const dispatched: string[] = [];
    const original = actions.dispatchSlashFromChat;
    actions.dispatchSlashFromChat = async (line: string) => {
      dispatched.push(line);
      return { kind: "handled" as const };
    };
    try {
      const setup = await renderWizard();
      actions.openConnectWizard(answers.length === 1 ? "login" : "source");
      await setup.renderOnce();
      for (const answer of answers) {
        if (answer) await setup.mockInput.typeText(answer);
        setup.mockInput.pressEnter();
        await setup.renderOnce();
      }
      setup.renderer.destroy();
    } finally {
      actions.dispatchSlashFromChat = original;
    }
    return dispatched;
  }

  it("the public-endpoint flow dispatches /source add, never /login", async () => {
    const dispatched = await answerAll(["rikyu", "https://rikyu.example/v1", "key-123", "openai", ""]);
    expect(dispatched).toEqual([
      "/source add rikyu --url https://rikyu.example/v1 --api-key key-123 --provider openai",
    ]);
  });

  it("the login flow still dispatches /login with the token", async () => {
    expect(await answerAll(["tok.en.value"])).toEqual(["/login tok.en.value"]);
  });
});
