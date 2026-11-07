/**
 * S4 (onboarding plan 2026-10-15 §1): a key typed on a `!` shell line —
 * `r4 source add … --api-key <k>`, `r4 auth login -k <jwt>` — runs as typed
 * in the user's shell, but never reaches the transcript, the model relay or
 * the server-owned queue (`session.queue_remote`): the line is masked and
 * the key is scrubbed from what the command printed. The title-screen path
 * (actions.submitChatText); tests/composer-secrets.test.tsx covers ChatPanel.
 * Runs against a temp R4_CONFIG_HOME, never the real config.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setDemoMode } from "../src/lib/console-mode";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";

const KEY = "sk-live-S4-shell-key-0123456789abcdef";
const JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzaGVsbCJ9.c2lnLXNoZWxs";
// `echo` prints the line back: the key is in the command AND its output.
const KEY_LINE = `!echo r4 source add rikyu --url https://r.example/v1 --api-key ${KEY}`;
const MASKED = "echo r4 source add rikyu --url https://r.example/v1 --api-key •••";

const saved = { home: process.env.R4_CONFIG_HOME, shell: process.env.SHELL };
const bridgeCalls: Array<{ method: string; params: unknown }> = [];
let home = "";

function restoreEnv(name: "R4_CONFIG_HOME" | "SHELL", value: string | undefined): void {
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}

function transcript(): string {
  return workspace.chatMessages.map((m) => m.content).join("\n");
}

function relayed(): string | undefined {
  return workspace.chatMessages.find((m) => m.role === "user" && m.content.startsWith("I ran `!"))?.content;
}

async function untilRelayed(): Promise<void> {
  for (let i = 0; i < 100 && !relayed(); i += 1) await new Promise((r) => setTimeout(r, 30));
}

beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), "r4-shell-"));
  process.env.R4_CONFIG_HOME = home;
  process.env.SHELL = "/bin/sh";
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  agentActions.resetAgentRuntime();
  resetWorkspace();
  bridgeCalls.length = 0;
  setCompanionBridgeForTests({
    request: async (method: string, params: unknown) => {
      bridgeCalls.push({ method, params });
      return { applied: true, queue: [1] };
    },
    on: () => () => undefined,
    off: () => undefined,
  } as never);
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  setWorkspace("chatBackend", null);
  setWorkspace("chatStreaming", false);
  resetWorkspace();
  restoreEnv("R4_CONFIG_HOME", saved.home);
  restoreEnv("SHELL", saved.shell);
  rmSync(home, { recursive: true, force: true });
});

describe("`!` line with a key — masked everywhere it is shown or relayed", () => {
  test("idle: the echo, the output and the model turn carry the masked line only", async () => {
    const sent: string[] = [];
    setWorkspace("chatBackend", {
      chat: async (message: string) => {
        sent.push(message);
        return "ok";
      },
    } as never);
    await actions.submitChatText(KEY_LINE);
    await untilRelayed();
    expect(relayed()).toContain(`I ran \`!${MASKED}\``);
    expect(transcript()).toContain(`$ ${MASKED}`);
    expect(transcript()).toContain("/source add and /login hand a key to r4 on stdin");
    expect(transcript()).not.toContain(KEY);
    expect(sent.length).toBeGreaterThan(0);
    expect(sent.join("\n")).not.toContain(KEY);
    expect(JSON.stringify(bridgeCalls)).not.toContain(KEY);
  });

  test("streaming: the relay queued on the server is masked", async () => {
    setWorkspace("chatStreaming", true);
    await actions.submitChatText(KEY_LINE);
    await untilRelayed();
    for (let i = 0; i < 50 && bridgeCalls.length === 0; i += 1) await new Promise((r) => setTimeout(r, 20));
    expect(bridgeCalls.map((c) => c.method)).toContain("session.queue_remote");
    expect(JSON.stringify(bridgeCalls)).not.toContain(KEY);
    expect(transcript()).not.toContain(KEY);
  });

  test("r4's short alias: `r4 auth login -k <jwt>` is masked the same way", async () => {
    await actions.submitChatText(`!echo r4 auth login -k ${JWT}`);
    await untilRelayed();
    expect(transcript()).toContain("$ echo r4 auth login -k •••");
    expect(transcript()).not.toContain(JWT);
  });

  test("a line without a key is shown as typed, with no masking note", async () => {
    await actions.submitChatText("!echo plain-shell-probe");
    await untilRelayed();
    expect(transcript()).toContain("$ echo plain-shell-probe");
    expect(transcript()).not.toContain("key masked");
  });
});
