/**
 * S4 (onboarding plan 2026-10-15 §1): the /connect menu seeds
 * `/source add <name> --url <url> --api-key <k>`. That line must register
 * the source through `r4 source add --api-key-stdin` (the key on stdin) and
 * the key must not appear in the transcript: neither the echo of the typed
 * line nor any notice. `/login <token>` gets the same echo masking.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { tokenizeSlashArgs } from "../src/commands/slash";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { setDemoMode } from "../src/lib/console-mode";
import { carriesSecret } from "../src/lib/secret-redact";
import { splitSlashWords } from "../src/lib/source-add-args";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { PROVIDER_KEY, makeFakeR4, restoreR4, useFakeR4 } from "./helpers/fake-r4";

const LINE = `/source add rikyu --url https://rikyu.example/v1 --api-key ${PROVIDER_KEY} --description "Rikyu institutional"`;

function transcript(): string {
  return workspace.chatMessages.map((m) => m.content).join("\n");
}

/** Any bridge call except the read-only post-add model probe (2026-10-21
 *  receipts) means the line leaked past the console (to the agent). */
function refusingBridge(calls: string[]) {
  return {
    request: async (method: string) => {
      calls.push(method);
      throw new Error("bridge must not be reached");
    },
  };
}

describe("/source add — key on stdin, never in the transcript", () => {
  const bridgeCalls: string[] = [];

  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
    bridgeCalls.length = 0;
    setCompanionBridgeForTests(refusingBridge(bridgeCalls) as never);
  });

  afterEach(() => {
    setCompanionBridgeForTests(null);
    restoreR4();
  });

  test("registers through r4 with --api-key-stdin; the key goes to stdin", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const result = await actions.dispatchSlashFromChat(LINE);
    expect(result.kind).toBe("handled");
    const argv = fake.argv();
    expect(argv.slice(0, 3)).toEqual(["source", "add", "rikyu"]);
    expect(argv).toContain("--api-key-stdin");
    expect(argv).toContain("Rikyu institutional");
    expect(argv.join(" ")).not.toContain(PROVIDER_KEY);
    expect(fake.stdin()).toBe(PROVIDER_KEY);
    expect(bridgeCalls).toEqual(["list_models"]);
    expect(workspace.listOverlay).toBeNull();
  });

  test("the transcript keeps a masked echo and the notice, never the key", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    await actions.dispatchSlashFromChat(LINE);
    const text = transcript();
    expect(text).not.toContain(PROVIDER_KEY);
    expect(text).toContain("/source add rikyu --url https://rikyu.example/v1 --api-key •••");
    expect(text).toContain("source rikyu registered");
  });

  test("an r4 failure that echoes the key is reported without it", async () => {
    const fake = makeFakeR4("echo");
    useFakeR4(fake.python);
    const result = await actions.dispatchSlashFromChat(LINE);
    expect(result.kind).toBe("error");
    expect(transcript()).toContain("source add rikyu failed");
    expect(transcript()).not.toContain(PROVIDER_KEY);
  });

  test("a malformed line never spawns r4 and its error names no key", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const result = await actions.dispatchSlashFromChat(`/source add rikyu --api-key ${PROVIDER_KEY}`);
    expect(result.kind).toBe("error");
    expect(fake.spawned()).toBe(false);
    expect(transcript()).toContain("usage: /source add");
    expect(transcript()).not.toContain(PROVIDER_KEY);
  });
});

describe("slash echo masking (recordUserSlashCommand)", () => {
  beforeEach(() => {
    resetWorkspace();
  });

  test("/login <token> and --api-key values are masked; other flags stay", () => {
    actions.recordUserSlashCommand("/login eyJhbGciOi.secret-token.sig");
    actions.recordUserSlashCommand(`/source add x --url https://x --api-key=${PROVIDER_KEY} --api-key-env OR_KEY`);
    const echoed = workspace.chatMessages.slice(-2).map((m) => m.content);
    expect(echoed[0]).toBe("/login •••");
    expect(echoed[1]).toBe("/source add x --url https://x --api-key=••• --api-key-env OR_KEY");
  });

  test("retract still finds the masked echo by the original line", () => {
    const line = `/source add x --url https://x --api-key ${PROVIDER_KEY}`;
    actions.recordUserSlashCommand(line);
    const before = workspace.chatMessages.length;
    actions.retractLastUserSlashCommand(line);
    expect(workspace.chatMessages.length).toBe(before - 1);
  });
});

describe("slash echo masking — every way a key can be typed (S4)", () => {
  const JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJlY2hvIn0.c2lnLWVjaG8";
  const BASE = "/source add rikyu --url https://rikyu.example/v1";

  beforeEach(() => {
    resetWorkspace();
    setCompanionBridgeForTests(refusingBridge([]) as never);
  });

  afterEach(() => {
    setCompanionBridgeForTests(null);
    restoreR4();
  });

  test("/login masks every argument — a pasted `Bearer <jwt>` too", () => {
    actions.recordUserSlashCommand(`/login Bearer ${JWT}`);
    expect(workspace.chatMessages.at(-1)?.content).toBe("/login •••");
  });

  const TAILS = [
    `--apikey=${PROVIDER_KEY}`,
    `--token ${PROVIDER_KEY}`,
    `-k ${PROVIDER_KEY}`,
    PROVIDER_KEY,
    // A key glued onto a misspelt flag (no `=`, no space): the whole word is
    // the "flag", so the unknown-flag notice must not name it.
    `--api-key:${PROVIDER_KEY}`,
    `--apikey${PROVIDER_KEY}`,
  ];
  for (const tail of TAILS) {
    test(`a line r4 never sees still echoes no key: ${tail.replace(PROVIDER_KEY, "<key>")}`, async () => {
      const fake = makeFakeR4("ok");
      useFakeR4(fake.python);
      const line = `${BASE} ${tail}`;
      const result = await actions.dispatchSlashFromChat(line);
      expect(result.kind).toBe("error");
      expect(fake.spawned()).toBe(false);
      // With several positionals any may be the key, so none is shown.
      expect(transcript()).toContain("--url https://rikyu.example/v1 •••");
      expect(transcript()).not.toContain(PROVIDER_KEY);
      expect(carriesSecret(line)).toBe(true);
    });
  }

  test("a quoted key with text after the quote is masked whole, and still reaches r4 on stdin", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    await actions.dispatchSlashFromChat(`${BASE} --api-key=sk"${PROVIDER_KEY} tail-secret"`);
    expect(fake.stdin()).toBe(`sk${PROVIDER_KEY} tail-secret`);
    expect(transcript()).toContain(`${BASE} --api-key=•••`);
    expect(transcript()).not.toContain("tail-secret");
    expect(transcript()).not.toContain(PROVIDER_KEY);
  });

  test("an unknown flag is named only when it has a plain flag shape", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    await actions.dispatchSlashFromChat(`${BASE} --descripton "Rikyu institutional"`);
    expect(transcript()).toContain("unknown flag --descripton.");
    resetWorkspace();
    await actions.dispatchSlashFromChat(`${BASE} --apikey${PROVIDER_KEY}`);
    expect(transcript()).toContain("unknown flag (not shown — it may hold a key).");
    expect(fake.spawned()).toBe(false);
  });

  test("an unterminated quote masks everything after `add`", () => {
    actions.recordUserSlashCommand(`${BASE} --api-key "${PROVIDER_KEY}`);
    expect(workspace.chatMessages.at(-1)?.content).toBe("/source add •••");
  });

  test("the name, url, provider, description and env-var name stay readable", () => {
    const line = `${BASE} --api-key-env OR_KEY --provider openai --description "Rikyu institutional" --allow-insecure-http`;
    actions.recordUserSlashCommand(line);
    expect(workspace.chatMessages.at(-1)?.content).toBe(line);
    expect(carriesSecret(line)).toBe(false);
  });
});

describe("splitSlashWords — the words the slash tokenizer sees, with raw spans", () => {
  const SAMPLES = [
    'add rikyu --url https://x --description "two words" --api-key=sk"a b"c',
    "add 'single quoted' \"\" --flag=\"in\\\"side\" tail",
    "  add   C:\\path\\to   --url=https://x  ",
    'add "" "x"',
  ];

  test("same values as tokenizeSlashArgs", () => {
    for (const sample of SAMPLES) {
      expect(splitSlashWords(sample).words.map((w) => w.value)).toEqual(tokenizeSlashArgs(sample));
    }
  });

  test("each span is exactly the raw word; an open quote is reported", () => {
    const text = 'add x --api-key=sk"a b"c';
    expect(splitSlashWords(text).words.map((w) => text.slice(w.start, w.end))).toEqual([
      "add",
      "x",
      '--api-key=sk"a b"c',
    ]);
    expect(splitSlashWords('add "open').unterminated).toBe(true);
    expect(() => tokenizeSlashArgs('add "open')).toThrow();
  });
});
