/**
 * S4 (onboarding plan 2026-10-15 §1) at the composer (ChatPanel):
 *  1. A draft holding a credential that is cleared with Esc stays out of
 *     the in-session recall (↑), not only off disk — `/login <jwt>` (seeded,
 *     as /connect does) and `!r4 auth login -k <jwt>` (r4's short alias).
 *  2. A `!` line holding a key, run with Enter, is masked in the transcript,
 *     the rendered frame and the background-task label.
 * Runs against a temp R4_CONFIG_HOME, never the real config.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { testRender } from "@opentui/solid";

import { ChatPanel } from "../src/components/chat";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { actions, resetWorkspace, workspace } from "../src/stores";

const JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJyZWNhbGwifQ.c2lnLXJlY2FsbA";
const KEY = "sk-live-S4-composer-key-0123456789";

const saved = { home: process.env.R4_CONFIG_HOME, shell: process.env.SHELL };
let home = "";

function restoreEnv(name: "R4_CONFIG_HOME" | "SHELL", value: string | undefined): void {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}

const tick = (ms = 120) => new Promise((resolve) => setTimeout(resolve, ms));

beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), "r4-composer-"));
  process.env.R4_CONFIG_HOME = home;
  process.env.SHELL = "/bin/sh";
  process.env.R4_TUI_BACKEND = "offline";
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(() => {
  resetWorkspace();
  restoreEnv("R4_CONFIG_HOME", saved.home);
  restoreEnv("SHELL", saved.shell);
  rmSync(home, { recursive: true, force: true });
});

type Setup = Awaited<ReturnType<typeof testRender>>;

async function settle(setup: Setup, ms?: number): Promise<string> {
  await tick(ms);
  await setup.renderOnce();
  return setup.captureCharFrame();
}

/** Put `line` in the composer (seeded or typed), clear it with Esc, press ↑. */
async function escThenRecall(line: string, seed: boolean): Promise<{ typed: string; recalled: string }> {
  const setup = await testRender(() => <ChatPanel />, { width: 200, height: 30 });
  await setup.renderOnce();
  if (seed) actions.setPendingChatDraft(line);
  else await setup.mockInput.typeText(line);
  const typed = await settle(setup);
  setup.mockInput.pressEscape();
  await settle(setup);
  setup.mockInput.pressArrow("up");
  const recalled = await settle(setup);
  setup.renderer.destroy();
  return { typed, recalled };
}

describe("an Esc'd credential draft is not recalled with ↑", () => {
  it("/login <jwt> (seeded by /connect)", async () => {
    const { typed, recalled } = await escThenRecall(`/login ${JWT}`, true);
    expect(typed).toContain(JWT.slice(0, 20)); // the draft did reach the composer
    expect(recalled).not.toContain(JWT.slice(0, 20));
  });

  it("!r4 auth login -k <jwt> (r4's short alias for --api-key)", async () => {
    const { typed, recalled } = await escThenRecall(`!r4 auth login -k ${JWT}`, false);
    expect(typed).toContain(JWT.slice(0, 20));
    expect(recalled).not.toContain(JWT.slice(0, 20));
  });

  it("an ordinary draft still comes back (recall is not broken)", async () => {
    const { recalled } = await escThenRecall("summarise the last run", false);
    expect(recalled).toContain("summarise the last run");
  });
});

describe("a `!` line holding a key, run from the composer", () => {
  it("is masked in the transcript, the frame and the task label", async () => {
    const setup = await testRender(() => <ChatPanel />, { width: 200, height: 40 });
    await setup.renderOnce();
    await setup.mockInput.typeText(`!echo r4 source add rikyu --url https://r.example/v1 --api-key ${KEY}`);
    await setup.renderOnce();
    setup.mockInput.pressEnter();
    for (let i = 0; i < 60 && !workspace.chatMessages.some((m) => m.content.startsWith("I ran `!")); i += 1) {
      await tick(50);
    }
    const frame = await settle(setup);
    setup.renderer.destroy();
    const texts = workspace.chatMessages.map((m) => m.content).join("\n");
    expect(texts).toContain("$ echo r4 source add rikyu --url https://r.example/v1 --api-key •••");
    expect(texts).toContain("I ran `!echo r4 source add rikyu --url https://r.example/v1 --api-key •••`");
    expect(texts).not.toContain(KEY);
    expect(frame).not.toContain(KEY.slice(0, 12));
    for (const task of workspace.backgroundTasks) expect(task.label).not.toContain("sk-live");
  });
});
