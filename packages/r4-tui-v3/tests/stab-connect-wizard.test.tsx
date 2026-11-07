/**
 * Stabilization lane `connect` (2026-10-21) — /connect wizard pins.
 *
 *  connect#5  an answer with an apostrophe (or a backslash before the closing
 *             quote) failed "unterminated quote": word() quoted only spaces
 *             and `"`, while the splitter also reads `'` as a quote. The
 *             builder must round-trip ANY string through splitSlashWords +
 *             parseSourceAddArgs — property-style test over tricky strings.
 *  connect#8  answering "no" (or Enter) to the G-4 question still dispatched
 *             an add r4 was certain to refuse.
 *  connect#4  the name question accepted an existing source name, and the
 *             re-add silently replaced that row (and its stored key).
 */
import { afterEach, beforeEach, describe, expect, it, test } from "bun:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { testRender } from "@opentui/solid";

import { App } from "../src/app";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { buildWizardLine, connectQuestions, quoteSlashWord } from "../src/lib/connect-wizard";
import { setDemoMode } from "../src/lib/console-mode";
import { resetResolvedPythonCache } from "../src/lib/resolve-python";
import { loginTokenOf } from "../src/lib/connect";
import { parseSourceAddArgs, splitSlashWords } from "../src/lib/source-add-args";
import { actions, resetWorkspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";

// ── connect#5: the line builder round-trips any string ─────────────────────

/** Deterministic PRNG (mulberry32) — the same "random" strings every run. */
function prng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const TRICKY = ["'", '"', "\\", " ", "\t", "-", "--", "=", "a", "Z", "9", "é", "→", "'\"", "\\'", '\\"', "$", "`"];

function trickyStrings(count: number): string[] {
  const rand = prng(20261021);
  const out = [
    "Bob's server",
    "it's",
    "a\\",
    "a b\\",
    "\\",
    "'",
    '"',
    "''",
    '"\'"',
    "back\\ slash",
    'say "hi" to Bob\'s \\ box',
    "--looks-like-a-flag",
    "--description=x",
    "-",
    "==",
  ];
  for (let i = 0; i < count; i += 1) {
    const len = 1 + Math.floor(rand() * 10);
    let s = "";
    for (let j = 0; j < len; j += 1) s += TRICKY[Math.floor(rand() * TRICKY.length)];
    out.push(s);
  }
  return out;
}

function parseLine(line: string) {
  const { words, unterminated } = splitSlashWords(line);
  expect(unterminated).toBe(false);
  expect(words.slice(0, 2).map((w) => w.value)).toEqual(["/source", "add"]);
  const parsed = parseSourceAddArgs(words.slice(2).map((w) => w.value));
  if (!parsed.ok) throw new Error(`${parsed.error} — line: ${line}`);
  return parsed.opts;
}

describe("wizard line round-trips ANY string (connect#5)", () => {
  test("quoteSlashWord: one word, read back exactly, for every tricky string", () => {
    for (const value of trickyStrings(400)) {
      const { words, unterminated } = splitSlashWords(quoteSlashWord(value));
      expect(unterminated).toBe(false);
      expect(words.map((w) => w.value)).toEqual([value]);
    }
  });

  test("description, provider and key reach the parsed options unchanged", () => {
    for (const value of trickyStrings(300)) {
      if (!value.trim()) continue; // the overlay trims; a blank optional is skipped
      const opts = parseLine(buildWizardLine("source", ["n", "https://h.example/v1", value, value, value]));
      expect(opts.description).toBe(value);
      expect(opts.provider).toBe(value);
      expect(opts.apiKey).toBe(value);
      expect(opts.name).toBe("n");
    }
  });

  test("the finding's own case: an apostrophe in the description, and in the name", () => {
    expect(parseLine(buildWizardLine("source", ["apos", "http://127.0.0.1:9/v1", "", "", "Bob's"])).description).toBe(
      "Bob's",
    );
    expect(parseLine(buildWizardLine("source", ["bob's", "http://127.0.0.1:9/v1", "", "", ""])).name).toBe("bob's");
  });

  test("a login token with quote characters round-trips through /login's parser", () => {
    for (const token of ["a'b", 'a"b', "a\\", "tok'\"\\en"]) {
      const line = buildWizardLine("login", [token]);
      expect(loginTokenOf(line.slice("/login ".length))).toBe(token);
    }
  });
});

// ── connect#4: the name question refuses a registered name ──────────────────

describe("source name question (connect#4)", () => {
  test("refuses a name `r4 source list` already has, and a flag-shaped name", () => {
    const name = connectQuestions("source", [], ["dup", "rikyu"])[0]!;
    expect(name.key).toBe("name");
    expect(name.validate!("dup")).toContain("already exists");
    expect(name.validate!("--x")).toContain("not a flag");
    expect(name.validate!("fresh")).toBeNull();
  });
});

// ── UI: G-4 refusal and the taken-name check, through the real overlay ──────

const savedPython = process.env.R4_TUI_PYTHON;
let fakeDir = "";

function useFakeSourceList(json: string): void {
  fakeDir = mkdtempSync(join(tmpdir(), "stab-wizard-"));
  const python = join(fakeDir, "python");
  writeFileSync(python, `#!/usr/bin/env bash\nshift 2\ncase "$1 $2" in\n  "source list") echo '${json}' ;;\nesac\n`);
  chmodSync(python, 0o755);
  process.env.R4_TUI_PYTHON = python;
  resetResolvedPythonCache();
}

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
  await new Promise((resolve) => setTimeout(resolve, 100));
  stopBackendPoller();
  stopKnowledgePoller();
  resetWorkspace();
  actions.closeAllOverlays();
  if (savedPython === undefined) delete process.env.R4_TUI_PYTHON;
  else process.env.R4_TUI_PYTHON = savedPython;
  resetResolvedPythonCache();
  if (fakeDir) rmSync(fakeDir, { recursive: true, force: true });
  fakeDir = "";
});

async function runWizard(answers: string[], settleMs = 0) {
  const dispatched: string[] = [];
  const original = actions.dispatchSlashFromChat;
  actions.dispatchSlashFromChat = async (line: string) => {
    dispatched.push(line);
    return { kind: "handled" as const };
  };
  const setup = await testRender(() => <App forceOffline={true} />, { width: 160, height: 60 });
  try {
    await setup.renderOnce();
    actions.closeAllOverlays();
    await setup.renderOnce();
    actions.openConnectWizard("source");
    await setup.renderOnce();
    if (settleMs) await new Promise((resolve) => setTimeout(resolve, settleMs));
    for (const answer of answers) {
      if (answer) await setup.mockInput.typeText(answer);
      setup.mockInput.pressEnter();
      await setup.renderOnce();
    }
    return { dispatched, frame: setup.captureCharFrame() };
  } finally {
    actions.dispatchSlashFromChat = original;
    setup.renderer.destroy();
  }
}

describe("wizard overlay (connect#8, connect#4)", () => {
  for (const no of ["no", ""]) {
    it(`G-4 answered ${no ? `"${no}"` : "with Enter"} runs nothing and goes back to the URL question`, async () => {
      useFakeSourceList("[]");
      const { dispatched, frame } = await runWizard(["g4no", "http://10.0.0.2:8000/v1", "key-123", "", "", no]);
      expect(dispatched).toEqual([]);
      expect(frame).toContain("not added — the key would travel plain http");
      expect(frame).toContain("Base URL");
      expect(frame).toContain("g4no");
    });
  }

  it("G-4 yes still dispatches the opted-in line", async () => {
    useFakeSourceList("[]");
    const { dispatched } = await runWizard(["g4yes", "http://10.0.0.2:8000/v1", "key-123", "", "", "yes"]);
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0]).toContain("--allow-insecure-http");
  });

  it("an existing source name is refused at the name question", async () => {
    useFakeSourceList(JSON.stringify([{ name: "dup", kind: "public", url: "https://d.example/v1" }]));
    const { dispatched, frame } = await runWizard(["dup"], 400);
    expect(dispatched).toEqual([]);
    expect(frame).toContain("source 'dup' already exists");
    expect(frame).toContain("1/5");
  });
});
