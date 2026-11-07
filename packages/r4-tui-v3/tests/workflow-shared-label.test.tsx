/**
 * 2026-10-23: a workflow another user shared with the caller (UMRS row
 * `shared: true`, owner's `scope: "user"`, `user_id` = owner) read "[user]"
 * on the /workflow list card, as if the caller owned it. Every surface that
 * labels a workflow by its scope now says "shared · <owner>" for such a row;
 * the caller's own rows keep "user", system rows keep "system".
 */
import { describe, it, expect, beforeEach, afterEach } from "bun:test";
import { testRender } from "@opentui/solid";
import { reconcile } from "solid-js/store";
import { MgmtCardView } from "../src/components/mgmt-card";
import { WorkflowDefOverlay } from "../src/components/workflow-def-overlay";
import { actions, resetWorkspace } from "../src/stores";
import { setUmrsWorkflowStore } from "../src/stores/umrs-workflow";

/** The workflow.list rows the bridge sends for researcher2 on rccs. */
const SHARED_ROW = {
  workflow_name: "example-track-v0.2.9",
  name: "example-track-v0.2.9",
  description: "example tracking",
  tags: ["shared · mnagaso"],
  scope: "user",
  owner: "mnagaso",
  shared: true,
  access: ["circle:lab/example-project-b", "project:example-project-b"],
};
const OWN_ROW = {
  workflow_name: "my-own-wf",
  name: "my-own-wf",
  description: "mine",
  tags: ["user"],
  scope: "user",
  owner: "researcher2",
  shared: false,
  access: [],
};
const SYSTEM_ROW = {
  workflow_name: "sys-wf",
  name: "sys-wf",
  description: "platform",
  tags: ["system"],
  scope: "system",
  owner: "admin",
  shared: false,
  access: [],
};

async function renderCard(card: unknown, height = 12) {
  const setup = await testRender(() => <MgmtCardView card={card as never} />, { width: 120, height });
  await setup.renderOnce();
  return setup;
}

beforeEach(() => {
  resetWorkspace();
  setUmrsWorkflowStore("catalog", []);
  setUmrsWorkflowStore("definitions", reconcile({}));
  setUmrsWorkflowStore("definitionErrors", reconcile({}));
});

afterEach(async () => {
  await new Promise((r) => setTimeout(r, 50));
  actions.setWorkflowDefId(null);
  setUmrsWorkflowStore("catalog", []);
  resetWorkspace();
});

describe("/workflow list card", () => {
  it("labels a shared row by its owner, an own row user, a system row system", async () => {
    // An older bridge (or a legacy fake) may still tag the shared row with the
    // owner's scope: the shared flag wins over the tags.
    const legacyTagged = { ...SHARED_ROW, tags: ["user"] };
    const setup = await renderCard({
      v: 1,
      type: "wf-list",
      payload: { workflows: [legacyTagged, OWN_ROW, SYSTEM_ROW], count: 3 },
    });
    const frame = setup.captureCharFrame();
    const lines = frame.split("\n");
    const sharedLine = lines.find((l) => l.includes("example-track-v0.2.9")) ?? "";
    expect(sharedLine).toContain("[shared · mnagaso]");
    expect(sharedLine).not.toContain("[user]");
    expect(lines.find((l) => l.includes("my-own-wf"))).toContain("[user]");
    expect(lines.find((l) => l.includes("sys-wf"))).toContain("[system]");
    setup.renderer.destroy();
  });
});

describe("/workflow show card", () => {
  const DESCRIBE = {
    workflow_name: "example-track-v0.2.9",
    name: "example-track-v0.2.9",
    version: "0.2.9",
    scope: "user",
    user_id: "mnagaso",
  };

  it("reads scope as shared · <owner> when the catalog marks the workflow shared", async () => {
    setUmrsWorkflowStore("catalog", [SHARED_ROW]);
    const setup = await renderCard({ v: 1, type: "wf-show", payload: DESCRIBE });
    const scopeLine = setup.captureCharFrame().split("\n").find((l) => l.includes("scope")) ?? "";
    expect(scopeLine).toContain("shared · mnagaso");
    expect(scopeLine).not.toMatch(/scope:\s*user/);
    setup.renderer.destroy();
  });

  it("keeps the caller's own scope when the catalog does not mark it shared", async () => {
    setUmrsWorkflowStore("catalog", [OWN_ROW]);
    const own = { ...DESCRIBE, workflow_name: "my-own-wf", name: "my-own-wf", user_id: "researcher2" };
    const setup = await renderCard({ v: 1, type: "wf-show", payload: own });
    const scopeLine = setup.captureCharFrame().split("\n").find((l) => l.includes("scope")) ?? "";
    expect(scopeLine).toMatch(/scope:\s*user/);
    expect(scopeLine).not.toContain("shared");
    setup.renderer.destroy();
  });
});

describe("workflow definition overlay", () => {
  const DEF = {
    workflow_name: "example-track-v0.2.9",
    version: "0.2.9",
    description: "example tracking",
    scope: "user",
    user_id: "mnagaso",
    steps: [{ name: "track", entrypoint: "track:Track" }],
    files: [],
  };

  async function open(height = 40) {
    setUmrsWorkflowStore("definitions", DEF.workflow_name, DEF as never);
    actions.setWorkflowDefId(DEF.workflow_name);
    const setup = await testRender(() => <WorkflowDefOverlay />, { width: 120, height });
    await setup.renderOnce();
    return setup;
  }

  it("tags a shared definition shared, with its owner", async () => {
    setUmrsWorkflowStore("catalog", [SHARED_ROW]);
    const setup = await open();
    const frame = setup.captureCharFrame();
    expect(frame).toContain("tags      · v0.2.9 · shared · owner mnagaso");
    expect(frame).not.toContain("· user ·");
    setup.renderer.destroy();
  });

  it("keeps the owner's scope when the catalog does not mark it shared", async () => {
    setUmrsWorkflowStore("catalog", [{ ...SHARED_ROW, shared: false }]);
    const setup = await open();
    expect(setup.captureCharFrame()).toContain("tags      · v0.2.9 · user · owner mnagaso");
    setup.renderer.destroy();
  });
});
