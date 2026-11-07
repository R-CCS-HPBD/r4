/**
 * c2 security lane F8 (2026-10-21): the prompt history was written 0644 in a
 * 0755 directory — any local user could read every prompt. The file is now
 * 0600 (also when an older, wider file or a stale .tmp already exists) and a
 * directory the history creates is 0700.
 */
import { afterEach, describe, expect, it } from "bun:test";
import { mkdirSync, mkdtempSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { appendPromptHistory } from "../src/lib/prompt-history";

const saved = process.env.R4_CONFIG_HOME;
let scratch = "";
afterEach(() => {
  if (saved === undefined) delete process.env.R4_CONFIG_HOME;
  else process.env.R4_CONFIG_HOME = saved;
  if (scratch) rmSync(scratch, { recursive: true, force: true });
});

const mode = (path: string) => statSync(path).mode & 0o777;

describe("prompt history file modes", () => {
  it("a fresh history: directory 0700, file 0600", () => {
    scratch = mkdtempSync(join(tmpdir(), "r4-hist-mode-"));
    const home = join(scratch, "cfg", "r4");
    process.env.R4_CONFIG_HOME = home;
    expect(appendPromptHistory("first prompt")).toBe(true);
    expect(mode(home)).toBe(0o700);
    expect(mode(join(home, "prompt-history.jsonl"))).toBe(0o600);
  });

  it("an existing 0644 file and a stale 0644 .tmp end up 0600 after the next write", () => {
    scratch = mkdtempSync(join(tmpdir(), "r4-hist-mode-"));
    const home = join(scratch, "r4");
    mkdirSync(home, { mode: 0o755 });
    const file = join(home, "prompt-history.jsonl");
    writeFileSync(file, `${JSON.stringify({ text: "old" })}\n`, { mode: 0o644 });
    writeFileSync(`${file}.tmp`, "stale", { mode: 0o644 });
    process.env.R4_CONFIG_HOME = home;
    expect(appendPromptHistory("second prompt")).toBe(true);
    expect(mode(file)).toBe(0o600);
  });
});
