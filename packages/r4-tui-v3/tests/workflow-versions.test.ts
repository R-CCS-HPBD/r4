/**
 * ADR-113 WV4 (2026-11-09): workflow versions on the console — the pure
 * helpers every surface shares (refs, row labels, the picker's cycle, run
 * labels) plus the card/list formatters that use them. Older servers send
 * no `versions`: nothing there may invent a choice or pin a version.
 */
import { describe, expect, test } from "bun:test";
import {
  catalogRowLabel,
  catalogRowRef,
  codeUnavailable,
  cycleVersion,
  defaultVersionOf,
  parseWorkflowRef,
  pickNextVersion,
  rowVersions,
  runDefinitionKey,
  runLabelWithDigest,
  shortDigest,
  versionCountLabel,
  versionListLabel,
  workflowRefLabel,
} from "../src/lib/workflow-versions";
import { triggerCommand } from "../src/lib/workflow-params";
import { cardTriggerCommand, cardVersion, workflowCardRows } from "../src/lib/workflow-card-rows";
import { wfDescribeRows, wfDetailSteps, wfListRows, wfStatusRows } from "../src/lib/mgmt-card-format";

const SHA = "3f2a9c1d0b7e55aa66bb77cc88dd99ee00ff11aa22bb33cc44dd55ee66ff7788";

describe("refs", () => {
  test("name@X splits on the last @; a bare name has no version", () => {
    expect(parseWorkflowRef("relax@1.2.0")).toEqual({ name: "relax", version: "1.2.0" });
    expect(parseWorkflowRef("relax")).toEqual({ name: "relax" });
    expect(parseWorkflowRef("  relax@2.0.0-rc.1 ")).toEqual({ name: "relax", version: "2.0.0-rc.1" });
    // Not a ref: an empty version or a leading @ keeps the text as the name.
    expect(parseWorkflowRef("relax@")).toEqual({ name: "relax@" });
    expect(parseWorkflowRef("@1.0.0")).toEqual({ name: "@1.0.0" });
  });

  test("labels: name@version, the bare name without one", () => {
    expect(workflowRefLabel("relax", "1.2.0")).toBe("relax@1.2.0");
    expect(workflowRefLabel("relax", undefined)).toBe("relax");
    expect(workflowRefLabel(null, "1.0.0")).toBe("");
  });

  test("short digest: 12 characters, empty when absent", () => {
    expect(shortDigest(SHA)).toBe("3f2a9c1d0b7e");
    expect(shortDigest(undefined)).toBe("");
  });
});

describe("catalog rows", () => {
  const row = { version: "1.2.0", versions: ["2.0.0-rc.1", "1.2.0", "1.1.0"] };

  test("default version and how many others", () => {
    expect(versionCountLabel(row)).toBe("v1.2.0 (+2)");
    expect(versionCountLabel({ version: "1.0.0", versions: ["1.0.0"] })).toBe("v1.0.0");
    // Older server: one version (or none), never a count.
    expect(versionCountLabel({ version: "0.1" })).toBe("v0.1");
    expect(versionCountLabel({})).toBe("");
    expect(rowVersions({ version: "0.1" })).toEqual(["0.1"]);
  });

  test("the default is the highest release; a pre-release only without one", () => {
    expect(defaultVersionOf(row.versions)).toBe("1.2.0");
    expect(defaultVersionOf(["2.0.0-rc.2", "2.0.0-rc.1"])).toBe("2.0.0-rc.2");
    expect(versionListLabel(row.versions, "1.2.0", "1.1.0")).toBe("2.0.0-rc.1 · 1.2.0 (default) · ▸1.1.0");
  });

  test("the picker cycles, wraps both ways, and refuses a single version", () => {
    expect(pickNextVersion(row, undefined, 1)).toBe("1.1.0");
    expect(pickNextVersion(row, "1.1.0", 1)).toBe("2.0.0-rc.1");
    expect(pickNextVersion(row, undefined, -1)).toBe("2.0.0-rc.1");
    expect(pickNextVersion({ version: "1.0.0", versions: ["1.0.0"] }, undefined, 1)).toBeUndefined();
    expect(pickNextVersion({}, undefined, 1)).toBeUndefined();
    expect(cycleVersion(["a", "b"], "zzz", 1)).toBe("b");
  });

  test("a non-default pick shows in the label and the ref; the default stays a bare name", () => {
    expect(catalogRowLabel("relax", row)).toBe("relax · v1.2.0 (+2)");
    expect(catalogRowLabel("relax", row, "1.1.0")).toBe("relax@1.1.0 · picked (+2)");
    expect(catalogRowLabel("relax", row, "1.2.0")).toBe("relax · v1.2.0 (+2)");
    expect(catalogRowRef("relax", row, "1.1.0")).toBe("relax@1.1.0");
    expect(catalogRowRef("relax", row, "1.2.0")).toBe("relax");
    expect(catalogRowLabel("old", {})).toBe("old");
  });

  test("/workflow list card: the version column", () => {
    const { rows } = wfListRows({
      workflows: [{ workflow_name: "relax", ...row }, { workflow_name: "old" }],
    });
    expect(rows[0]![3]).toBe("v1.2.0 (+2)");
    expect(rows[1]![3]).toBe("");
  });
});

describe("runs", () => {
  test("run labels carry name@version and the digest", () => {
    const run = { workflow_name: "relax", workflow_version: "1.1.0", source_sha256: SHA };
    expect(runLabelWithDigest(run)).toBe("relax@1.1.0 · 3f2a9c1d0b7e");
    expect(runLabelWithDigest({ workflow_name: "relax" })).toBe("relax");
    expect(runLabelWithDigest({})).toBe("workflow");
    expect(runDefinitionKey(run)).toBe("relax@1.1.0");
    expect(runDefinitionKey({ workflow_name: "relax" })).toBe("relax");
    expect(runDefinitionKey({})).toBeUndefined();
  });

  test("status / run cards: workflow row is name@version, then a digest row", () => {
    const rows = wfStatusRows({
      execution_id: "exec-1",
      workflow_name: "relax",
      workflow_version: "1.1.0",
      source_sha256: SHA,
      status: "running",
    });
    expect(rows.map((r) => r.key)).toEqual(["execution", "workflow", "digest", "status"]);
    expect(rows[1]!.value).toBe("relax@1.1.0");
    expect(rows[2]!.value).toBe("3f2a9c1d0b7e");
    const { header } = wfDetailSteps({ execution: { workflow_name: "relax", workflow_version: "1.1.0" } });
    expect(header.find((r) => r.key === "workflow")?.value).toBe("relax@1.1.0");
  });
});

describe("describe card (/workflow show)", () => {
  test("versions listed with the default marked, digest short, gone code marked", () => {
    const rows = wfDescribeRows({
      workflow_name: "relax",
      version: "1.1.0",
      versions: ["1.2.0", "1.1.0"],
      source_sha256: SHA,
      code: "unavailable",
    });
    const map = Object.fromEntries(rows.map((r) => [r.key, r.value]));
    expect(map["versions"]).toBe("1.2.0 (default) · ▸1.1.0");
    expect(map["source_sha256"]).toBe("3f2a9c1d0b7e");
    expect(map["code"]).toBe("unavailable — this version cannot run");
    expect(codeUnavailable({ code: "available" })).toBe(false);
  });
});

describe("trigger line and definition card", () => {
  const BASE = {
    workflow_name: "relax",
    parameter_schema: { type: "object", properties: { cif: { type: "string" } }, required: ["cif"] },
    steps: [{ name: "run", entrypoint: "run:Run" }],
    files: [],
  };

  test("triggerCommand pins name@version only when given one", () => {
    expect(triggerCommand("relax", [], "1.1.0")).toBe("/workflow trigger relax@1.1.0");
    expect(triggerCommand("relax", [])).toBe("/workflow trigger relax");
  });

  test("a versioned server: the card pins the version it shows and lists the others", () => {
    const d = { ...BASE, version: "1.1.0", versions: ["1.2.0", "1.1.0"], source_sha256: SHA };
    expect(cardVersion(d)).toBe("1.1.0");
    expect(cardTriggerCommand(d).command).toBe("/workflow trigger relax@1.1.0 --param cif=");
    const text = workflowCardRows(d, { width: 100, expanded: null, cursor: 0 }).map((r) => r.text);
    expect(text).toContain("versions  · 1.2.0 (default) · ▸1.1.0  — v / V picks another");
    expect(text).toContain("digest    · 3f2a9c1d0b7e");
    expect(text.some((t) => t.startsWith("code      · unavailable"))).toBe(false);
  });

  test("a version whose code is gone is marked on the card", () => {
    const d = { ...BASE, version: "1.0.0", versions: ["1.1.0", "1.0.0"], code: "unavailable" };
    const rows = workflowCardRows(d, { width: 100, expanded: null, cursor: 0 });
    const gone = rows.find((r) => r.text.startsWith("code      · unavailable"));
    expect(gone?.tone).toBe("bright");
  });

  test("an older server (no versions): no pin, no version rows", () => {
    const d = { ...BASE, version: "0.1" };
    expect(cardVersion(d)).toBeUndefined();
    expect(cardTriggerCommand(d).command).toBe("/workflow trigger relax --param cif=");
    const text = workflowCardRows(d, { width: 100, expanded: null, cursor: 0 }).map((r) => r.text);
    expect(text.some((t) => t.startsWith("versions"))).toBe(false);
  });
});
