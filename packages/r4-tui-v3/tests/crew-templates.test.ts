/**
 * ADR-092 slice 4: crews are plan templates in the console.
 *
 * `/teamup on <crew>` for any crew but hpc resolves the crew (or an alias)
 * through the bridge's `teamup.crews` — the one crew → template map — and
 * arms that template's plan lane, exactly as `/teamup on plan:<template>`;
 * the next chat message runs `team.plan_run`, never `teamup.start`. `hpc`
 * still arms the teamup session. `/teamup crews` names each crew's template.
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { teamsActions } from "../src/stores/teams";

type Call = { method: string; params: Record<string, unknown> };

const CREWS = {
  crews: [
    { name: "hpc", template: null, aliases: [], description: "The HPC optimisation preset. More." },
    { name: "code", template: "code", aliases: ["coding", "dev"], description: "The code crew. More." },
    { name: "paper-writing", template: "paper-writing", aliases: ["manuscript", "paper"], description: "Paper." },
    { name: "research", template: "research-round", aliases: ["science"], description: "Research." },
  ],
  count: 4,
};
const TEMPLATES = {
  templates: [
    { name: "code", params: { goal: { required: true } } },
    { name: "paper-writing", params: { subject: { required: true } } },
    { name: "research-round", params: { objective: { required: true } } },
  ],
};

function fakeBridge(replies: Record<string, unknown>) {
  const calls: Call[] = [];
  return {
    calls,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return replies[method] ?? {};
    },
    respondApproval: () => undefined,
    on: () => undefined,
  };
}

beforeEach(() => resetWorkspace());
afterEach(() => resetWorkspace());

describe("/teamup on <crew> (slice 4)", () => {
  it("a template crew arms its plan, and the next message runs team.plan_run", async () => {
    const bridge = fakeBridge({
      "teamup.crews": CREWS,
      "team.plan_templates": TEMPLATES,
      "team.plan_run": { ok: true, run_id: "run-3", plan: "paper-writing", status: "running" },
    });
    setCompanionBridgeForTests(bridge as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "teamup", args: "on paper" });
    expect(workspace.teamupArmedPlan).toEqual({ template: "paper-writing", args: {} });

    actions.sendChatMessage("spin qubit coherence");
    await new Promise((r) => setTimeout(r, 40));
    const run = bridge.calls.find((c) => c.method === "team.plan_run");
    expect(run?.params).toEqual({ template: "paper-writing", goal: "spin qubit coherence" });
    expect(bridge.calls.some((c) => c.method === "teamup.start")).toBe(false);
  });

  it("the default crew is research, which is the research-round template", async () => {
    setCompanionBridgeForTests(fakeBridge({ "teamup.crews": CREWS, "team.plan_templates": TEMPLATES }) as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "teamup", args: "on" });
    expect(workspace.teamupArmedPlan?.template).toBe("research-round");
  });

  it("hpc still arms the teamup session", async () => {
    const bridge = fakeBridge({ "teamup.crews": CREWS });
    setCompanionBridgeForTests(bridge as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "teamup", args: "on hpc 2" });
    expect(workspace.teamupArmed).toBe(true);
    expect(workspace.teamupArmedPlan).toBeNull();
    expect(workspace.teamupArmedCrew).toBe("hpc");
    expect(workspace.teamupArmedParallel).toBe(2);
    expect(bridge.calls.some((c) => c.method === "teamup.crews")).toBe(false);
  });

  it("an unknown crew is refused by name and nothing is armed", async () => {
    setCompanionBridgeForTests(fakeBridge({ "teamup.crews": CREWS }) as never);
    const { handleWaveIntercepts } = await import("../src/commands/wave-intercepts");
    await handleWaveIntercepts({ name: "teamup", args: "on banana" });
    expect(workspace.teamupArmed).toBe(false);
    expect(workspace.chatMessages.at(-1)?.content).toContain("crew must be hpc|code|paper-writing|research");
  });

  it("/teamup crews names the template each crew runs", async () => {
    setCompanionBridgeForTests(fakeBridge({ "teamup.crews": CREWS }) as never);
    await teamsActions.crews();
    const text = String(workspace.chatMessages.at(-1)?.content ?? "");
    expect(text).toContain("hpc → teamup session");
    expect(text).toContain("research → plan research-round (also science)");
    expect(text).toContain("code → plan code (also coding, dev) — The code crew");
  });
});
