/**
 * S4 (onboarding plan 2026-10-15 §1): a key typed into the console must not
 * be persisted. The composer keeps a shared prompt history on disk
 * (prompt-history.jsonl); Esc on a draft writes the draft there. A line that
 * holds a credential (`/login <token>`, `--api-key <key>`) is never written,
 * and one written before this fix is dropped on load and purged on the next
 * write. Runs against a temp R4_CONFIG_HOME, never the real config.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { appendPromptHistory, promptHistory } from "../src/lib/prompt-history";
import { PROVIDER_KEY } from "./helpers/fake-r4";

const JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJoaXN0In0.c2lnLWhpc3Q";

const savedHome = process.env.R4_CONFIG_HOME;
let home = "";

function fileText(): string {
  try {
    return readFileSync(join(home, "prompt-history.jsonl"), "utf8");
  } catch {
    return "";
  }
}

beforeEach(() => {
  home = mkdtempSync(join(tmpdir(), "r4-hist-"));
  process.env.R4_CONFIG_HOME = home;
});

afterEach(() => {
  if (savedHome === undefined) delete process.env.R4_CONFIG_HOME;
  else process.env.R4_CONFIG_HOME = savedHome;
  rmSync(home, { recursive: true, force: true });
});

describe("prompt history never stores a credential", () => {
  test("/login <token> and --api-key lines are refused; ordinary lines are kept", () => {
    expect(appendPromptHistory(`/login ${JWT}`)).toBe(false);
    expect(appendPromptHistory(`/source add rikyu --url https://r.example --api-key ${PROVIDER_KEY}`)).toBe(false);
    expect(appendPromptHistory(`!r4 source add x --url https://x --api-key=${PROVIDER_KEY}`)).toBe(false);
    expect(appendPromptHistory("/source add x --url https://x --api-key-env OR_KEY")).toBe(true);
    expect(appendPromptHistory("summarise the last run")).toBe(true);
    const text = fileText();
    expect(text).not.toContain(JWT);
    expect(text).not.toContain(PROVIDER_KEY);
    expect(promptHistory()).toEqual([
      "/source add x --url https://x --api-key-env OR_KEY",
      "summarise the last run",
    ]);
  });

  test("r4's `-k` alias (and --token) on a `!` line is refused; `-k` elsewhere is kept", () => {
    expect(appendPromptHistory(`!r4 auth login -k ${JWT}`)).toBe(false);
    expect(appendPromptHistory(`!r4 login -k ${JWT}`)).toBe(false);
    expect(appendPromptHistory(`!r4 login -k${JWT}`)).toBe(false);
    expect(appendPromptHistory(`!r4 model stage qwen --token hf_${PROVIDER_KEY}`)).toBe(false);
    expect(appendPromptHistory("!curl -k https://gw.example/ready")).toBe(true);
    expect(appendPromptHistory("!r4 knowledge query -k 5 dislocation")).toBe(true);
    const text = fileText();
    expect(text).not.toContain(JWT);
    expect(text).not.toContain(PROVIDER_KEY);
  });

  test("a /source add line with a misspelt key flag or a stray word is refused", () => {
    const base = "/source add rikyu --url https://r.example/v1";
    for (const tail of [`--apikey=${PROVIDER_KEY}`, `--token ${PROVIDER_KEY}`, `-k ${PROVIDER_KEY}`, PROVIDER_KEY]) {
      expect(appendPromptHistory(`${base} ${tail}`)).toBe(false);
    }
    expect(appendPromptHistory(`/login Bearer ${JWT}`)).toBe(false);
    expect(appendPromptHistory(`${base} --api-key-env OR_KEY --description "Rikyu institutional"`)).toBe(true);
    const text = fileText();
    expect(text).not.toContain(PROVIDER_KEY);
    expect(text).not.toContain(JWT);
  });

  test("a key line written before the fix is not recalled and is purged on the next write", () => {
    writeFileSync(
      join(home, "prompt-history.jsonl"),
      [
        JSON.stringify({ text: "first prompt" }),
        JSON.stringify({ text: `/login ${JWT}` }),
        `/source add old --url https://o.example --api-key ${PROVIDER_KEY}`, // older plain row
        "",
      ].join("\n"),
    );
    expect(promptHistory()).toEqual(["first prompt"]);
    expect(appendPromptHistory("second prompt")).toBe(true);
    const text = fileText();
    expect(text).not.toContain(JWT);
    expect(text).not.toContain(PROVIDER_KEY);
    expect(promptHistory()).toEqual(["first prompt", "second prompt"]);
  });
});
