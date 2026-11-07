/**
 * ADR-104 D-3: `/settings rules` lists and removes the companion's rules
 * through `permission_rules.list` / `permission_rules.remove` — no rule file
 * is read or written by the console. `rm abc` is an invalid index (it used to
 * claim "removed rule #abc").
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { resetWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { setDemoMode } from "../src/lib/console-mode";
import { formatRulesListing, parseRuleIndex, parseRulesListing } from "../src/lib/settings-rules";

const RULES = [
  { index: 0, behavior: "deny", rule: "shell_exec(rm *)", source: "user", removable: true },
  { index: 1, behavior: "allow", rule: "file_write", source: "user_project", removable: true },
  { index: 2, behavior: "ask", rule: "git_push", source: "project", removable: false },
];

let calls: Array<[string, unknown]> = [];
let rules = [...RULES];
const savedBackend = process.env.R4_TUI_BACKEND;

function listing() {
  return { project_id: "proj-r", rules: rules.map((r, index) => ({ ...r, index })) };
}

beforeEach(() => {
  process.env.R4_TUI_BACKEND = "companion";
  setDemoMode(false);
  resetWorkspace();
  calls = [];
  rules = [...RULES];
  setCompanionBridgeForTests({
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push([method, params]);
      if (method === "permission_rules.remove") {
        const index = Number(params.index);
        if (!rules[index]?.removable) throw new Error(`rule ${index} is not removable`);
        rules = rules.filter((_, i) => i !== index);
      }
      return listing();
    },
  } as never);
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
  else process.env.R4_TUI_BACKEND = savedBackend;
  resetWorkspace();
});

const notices = () => workspace.chatMessages.filter((m) => m.role === "system").map((m) => m.content);

async function slash(line: string) {
  const { dispatchSlashCommand } = await import("../src/commands/slash");
  return dispatchSlashCommand(line);
}

describe("/settings rules", () => {
  it("lists through permission_rules.list and prints each rule's source", async () => {
    const result = await slash("/settings rules");
    expect(result.kind).toBe("handled");
    expect(calls).toEqual([["permission_rules.list", {}]]);
    const text = notices().at(-1) ?? "";
    expect(text).toContain("project proj-r");
    expect(text).toContain("0. deny  shell_exec(rm *) — user (~/.config/r4/permissions.json)");
    expect(text).toContain("1. allow file_write — user, this project");
    expect(text).toContain("2. ask   git_push — workspace .r4/permissions.json (checked in) · not removable here");
  });

  it("rm <n> removes through permission_rules.remove", async () => {
    await slash("/settings rules rm 1");
    expect(calls).toEqual([["permission_rules.remove", { index: 1 }]]);
    expect(notices().at(-1)).toContain("removed rule #1 — 2 rule(s) remain");
  });

  it("rm abc is an invalid index, nothing is sent", async () => {
    const result = await slash("/settings rules rm abc");
    expect(result).toEqual({ kind: "error", message: "invalid rule index: abc — use a number from /settings rules" });
    expect(calls).toEqual([]);
    expect(notices().some((n) => n.includes("removed rule"))).toBe(false);
  });

  it("a rule the companion refuses to remove reports its error", async () => {
    const result = await slash("/settings rules rm 2");
    expect(result.kind).toBe("error");
    expect((result as { message: string }).message).toContain("not removable");
  });

  it("clear removes the removable rules, highest index first", async () => {
    await slash("/settings rules clear");
    expect(calls).toEqual([
      ["permission_rules.list", {}],
      ["permission_rules.remove", { index: 1 }],
      ["permission_rules.remove", { index: 0 }],
    ]);
    expect(rules.map((r) => r.rule)).toEqual(["git_push"]);
  });
});

describe("/settings matrix (ADR-104 D-1 table)", () => {
  it("shows what each mode runs and the current badge", async () => {
    await slash("/settings matrix");
    const text = notices().at(-1) ?? "";
    expect(text).toContain("chat        read-only tools run; every other tool asks");
    expect(text).toContain("plan        read-only tools only; other tools are refused");
    expect(text).toContain("autopilot   every tool runs; it asks only before reading credentials");
    expect(text).toContain("current: mode:chat");
    expect(text).not.toContain("ask_always");
  });
});

describe("pure helpers", () => {
  it("parseRuleIndex accepts whole numbers only", () => {
    expect(parseRuleIndex("3")).toBe(3);
    expect(parseRuleIndex("abc")).toBeNull();
    expect(parseRuleIndex("-1")).toBeNull();
    expect(parseRuleIndex("1.5")).toBeNull();
    expect(parseRuleIndex(undefined)).toBeNull();
  });

  it("an empty or odd answer lists no rules", () => {
    expect(parseRulesListing(null)).toEqual({ projectId: "", rules: [] });
    expect(formatRulesListing({ projectId: "", rules: [] })).toContain("(none");
  });
});
