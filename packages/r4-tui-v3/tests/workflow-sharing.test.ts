/** 2026-10-23: the pure ownership-label helpers behind the shared workflow labels. */
import { test, expect } from "bun:test";
import { wfListRows } from "../src/lib/mgmt-card-format";
import {
  describeWithSharing,
  isSharedInCatalog,
  sharedLabel,
  workflowScopeLabel,
} from "../src/lib/workflow-sharing";

const CATALOG = [
  { workflow_name: "shared-wf", owner: "mnagaso", shared: true },
  { workflow_name: "mine", owner: "researcher2", shared: false },
];

test("sharedLabel names the owner when known", () => {
  expect(sharedLabel("mnagaso")).toBe("shared · mnagaso");
  expect(sharedLabel("")).toBe("shared");
  expect(sharedLabel(undefined)).toBe("shared");
});

test("workflowScopeLabel: the shared flag wins, else the tags as before", () => {
  expect(workflowScopeLabel({ shared: true, owner: "mnagaso", tags: ["user"] })).toBe("shared · mnagaso");
  expect(workflowScopeLabel({ shared: false, owner: "me", tags: ["user"] })).toBe("user");
  expect(workflowScopeLabel({ tags: ["system"] })).toBe("system");
  expect(workflowScopeLabel({ tags: ["a", "b", "c", "d"] })).toBe("a,b,c");
  expect(workflowScopeLabel({})).toBe("");
});

test("wfListRows puts the shared label in the tag column", () => {
  const { rows } = wfListRows({
    workflows: [{ workflow_name: "shared-wf", tags: ["user"], scope: "user", owner: "mnagaso", shared: true }],
  });
  expect(rows[0]![1]).toBe("shared · mnagaso");
});

test("isSharedInCatalog matches name and owner, both required", () => {
  expect(isSharedInCatalog(CATALOG, "shared-wf", "mnagaso")).toBe(true);
  // An entry with no owner (legacy user_id "") never borrows another owner's shared row.
  expect(isSharedInCatalog(CATALOG, "shared-wf", undefined)).toBe(false);
  expect(isSharedInCatalog(CATALOG, "shared-wf", "")).toBe(false);
  expect(isSharedInCatalog(CATALOG, "shared-wf", "someone-else")).toBe(false);
  expect(isSharedInCatalog(CATALOG, "mine", "researcher2")).toBe(false);
  expect(isSharedInCatalog(CATALOG, "", "mnagaso")).toBe(false);
  expect(isSharedInCatalog([], "shared-wf", "mnagaso")).toBe(false);
});

test("describeWithSharing rewrites only a shared describe payload's scope", () => {
  const shared = { name: "shared-wf", scope: "user", user_id: "mnagaso", version: "1" };
  expect(describeWithSharing(shared, CATALOG)).toEqual({ ...shared, scope: "shared · mnagaso" });
  const own = { name: "mine", scope: "user", user_id: "researcher2" };
  expect(describeWithSharing(own, CATALOG)).toBe(own);
  expect(describeWithSharing(null, CATALOG)).toBe(null);
});

test("the /workflow list card's rows refresh the catalog the show card and overlay read", async () => {
  const { umrsWorkflowActions, umrsWorkflowStore } = await import("../src/stores/umrs-workflow");
  umrsWorkflowActions.setCatalog([{ workflow_name: "shared-wf", owner: "mnagaso", shared: true } as never]);
  expect(umrsWorkflowStore.catalog.map((row) => row.workflow_name)).toEqual(["shared-wf"]);
  umrsWorkflowActions.setCatalog(undefined); // a malformed payload keeps what was there
  expect(umrsWorkflowStore.catalog.length).toBe(1);
  umrsWorkflowActions.setCatalog([]);
});
