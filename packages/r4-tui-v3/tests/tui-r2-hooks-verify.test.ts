/**
 * TUI audit r2 (2026-10-17), hooks lane — #63: with UMRS_BASE_URL set, the
 * Live verify answer was a canned "session persisted" line (un-awaited
 * fetch); #64: Ctrl+u reported "Finding upserted" when nothing persisted
 * (the Ctrl+u action itself is retired since 2026-10-18, H-10.1 follow-up).
 */
import { afterAll, afterEach, beforeEach, describe, expect, it } from "bun:test";
import { actions, resetWorkspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import * as researchKeyboard from "../src/lib/research-keyboard";
import { defaultBindings } from "../src/keymap";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const ENV_KEYS = ["UMRS_BASE_URL", "UMRS_URL", "R4_TUI_BACKEND", "R4_TUI_ALLOW_DEMO_VERIFY"];
const saved = Object.fromEntries(ENV_KEYS.map((k) => [k, process.env[k]]));
const realFetch = globalThis.fetch;
let fetchCalls: string[] = [];
const fetchReply = (): Response => new Response("{}", { status: 200 });

beforeEach(() => {
  for (const key of ENV_KEYS) delete process.env[key];
  fetchCalls = [];
  globalThis.fetch = (async (url: string | URL | Request) => {
    fetchCalls.push(String(url));
    return fetchReply();
  }) as typeof fetch;
  resetWorkspace();
  actions.setSpace("research");
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

afterAll(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

describe("Live verify with UMRS_BASE_URL set", () => {
  it("returns the gated stub (falls through to CLI verify), never a canned answer", () => {
    process.env["UMRS_BASE_URL"] = "http://127.0.0.1:9";
    const answer = agentActions.queryVerification("what supports H7?");
    expect(answer.answer).toContain("not connected to UMRS");
    expect(answer.answer).not.toContain("session persisted");
    expect(fetchCalls).toEqual([]);
  });
});

// H-10.1 follow-up (2026-10-18): the Ctrl+u "resolve as finding" action is
// retired. It posted to /umrs/verification/chat/resolve, which never
// persisted anything (503, then 410 since umrs d4a8ad85, and the route is
// now deleted). Findings belong to a project's verification reports
// (GET/PATCH /umrs/knowledge/findings); the TUI no longer pretends to write one.
describe("verify resolve retired", () => {
  const srcRoot = join(import.meta.dir, "../src");

  function sourceFiles(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) return sourceFiles(path);
      return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
    });
  }

  it("binds no key to a resolve command in any space", () => {
    const resolve = defaultBindings().filter((b) => b.cmd.includes("verify.resolve"));
    expect(resolve).toEqual([]);
    const researchCtrlU = defaultBindings().filter(
      (b) => b.space === "research" && /^ctrl\+(shift\+)?u$/.test(b.key),
    );
    expect(researchCtrlU).toEqual([]);
  });

  it("exposes no resolve action", () => {
    expect("handleVerifyResolve" in researchKeyboard).toBe(false);
    expect("resolveVerificationFinding" in agentActions).toBe(false);
  });

  it("no source file calls the retired /umrs/verification routes", () => {
    const callers = sourceFiles(srcRoot).filter((file) =>
      readFileSync(file, "utf8").includes("/umrs/verification"),
    );
    expect(callers).toEqual([]);
  });

  it("the Live verify pane no longer advertises Ctrl+u", () => {
    for (const file of ["components/verification-chat.tsx", "lib/research-keyboard.ts"]) {
      expect(readFileSync(join(srcRoot, file), "utf8")).not.toMatch(/Ctrl\+u/i);
    }
  });
});
