/**
 * /skills overlay churn fixes:
 *  1. toggle sends active: !current (server default true made deactivation
 *     impossible — "activation works, deactivation impossible").
 *  2. Inventory merge prefers rows that KNOW activation state, so a stateless
 *     CLI poll can never stomp bridge ●/○ flags between clicks (the
 *     "elements and length change every time" class).
 *  3. Store sequence: bridge snapshot REPLACE + CLI poll MERGE, in any order,
 *     keeps count stable and flags intact.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { mergeSkillInventories, visibleSkillRows } from "../src/lib/skill-inventory";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { skillActivationParams } from "../src/components/list-overlay";
import type { UiSkill } from "../src/types";

const bridgeSkill = (name: string, active: boolean, tools = 3): UiSkill => ({
  name,
  source: "builtin",
  hasMcp: false,
  description: `${name} desc`,
  active,
  tools: Array.from({ length: tools }, (_, i) => `tool${i}`),
});

const cliSkill = (name: string): UiSkill => ({
  name,
  source: "local",
  hasMcp: false,
  description: `${name} desc`,
  active: undefined,
  tools: undefined,
});

beforeEach(() => {
  resetWorkspace();
});

describe("skill activation toggle payload", () => {
  it("sends explicit active false to deactivate", () => {
    expect(skillActivationParams("academic-plotting", false)).toEqual({
      name: "academic-plotting",
      active: false,
    });
  });

  it("sends explicit active true to activate", () => {
    expect(skillActivationParams("analysis", true)).toEqual({
      name: "analysis",
      active: true,
    });
  });
});

describe("inventory merge collision rules", () => {
  it("bridge row with active flag beats stateless CLI copy on the same name", () => {
    const merged = mergeSkillInventories([cliSkill("foo")], [bridgeSkill("foo", true)]);
    expect(merged.length).toBe(1);
    expect(merged[0]!.active).toBe(true);
    expect(merged[0]!.tools?.length).toBe(3);
  });

  it("order does not matter — stateless never stomps known state", () => {
    const merged = mergeSkillInventories([bridgeSkill("foo", false)], [cliSkill("foo")]);
    expect(merged.length).toBe(1);
    expect(merged[0]!.active).toBe(false);
  });

  it("disjoint names union, sorted alphabetically", () => {
    const merged = mergeSkillInventories(
      [bridgeSkill("zed", true)],
      [cliSkill("alpha"), cliSkill("mid")],
    );
    expect(merged.map((s) => s.name)).toEqual(["alpha", "mid", "zed"]);
  });
});

describe("overlay visibility (the churn fix)", () => {
  it("bridge-known rows outrank local rows — only togglable rows are listed", () => {
    const merged = mergeSkillInventories([cliSkill("azure-cost")], [bridgeSkill("analysis", false)]);
    const shown = visibleSkillRows(merged);
    expect(shown.map((s) => s.name)).toEqual(["analysis"]);
  });

  it("falls back to the full merged list when no bridge rows exist (offline/down)", () => {
    const merged = mergeSkillInventories([cliSkill("azure-cost"), cliSkill("azure-prepare")]);
    const shown = visibleSkillRows(merged);
    expect(shown.map((s) => s.name)).toEqual(["azure-cost", "azure-prepare"]);
  });

  it("visible list is STABLE across click-replace + poll-merge cycles", () => {
    // The reported churn: open(27) → click(55) → click(49)… Three inventories
    // (CLI scan, loadWorkspace snapshot, list_skills) disagree on membership;
    // the visible rows must not track that noise.
    actions.setBackendState({ skills: [cliSkill("azure-cost"), cliSkill("azure-prepare")] });
    actions.setSkillInventory([bridgeSkill("analysis", false), bridgeSkill("academic-plotting", false)]);
    const atOpen = visibleSkillRows(workspace.skills).map((s) => `${s.name}:${s.active}`);

    // click → REPLACE from activate_skill snapshot (marks one active)
    actions.setSkillInventory([bridgeSkill("analysis", false), bridgeSkill("academic-plotting", true)]);
    const afterToggle = visibleSkillRows(workspace.skills).map((s) => `${s.name}:${s.active}`);

    // poll → MERGE another CLI pass (the old stomp source)
    actions.setBackendState({ skills: [cliSkill("azure-cost"), cliSkill("azure-prepare")] });
    const afterPoll = visibleSkillRows(workspace.skills).map((s) => `${s.name}:${s.active}`);

    expect(afterToggle).toEqual(afterPoll);
    expect(afterPoll).toEqual(["academic-plotting:true", "analysis:false"]);
    expect(atOpen).toEqual(["academic-plotting:false", "analysis:false"]);
  });
});
