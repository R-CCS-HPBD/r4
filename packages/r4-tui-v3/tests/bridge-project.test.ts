/**
 * Bridge mapping + reconnect policy tests.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { mapBridgeStateToWorkspace } from "../src/bridge/map-state";
import { CompanionBridgeClient } from "../src/bridge/jsonl-client";
import { resetWorkspace } from "../src/stores";
import {
  PROJECT_SUBCOMMANDS,
  parseSlashLine,
  filterSlashCommands,
  tokenizeSlashArgs,
} from "../src/commands/slash";

beforeEach(() => {
  resetWorkspace();
});

describe("mapBridgeStateToWorkspace", () => {
  it("maps known dashboard endpoints via existing mappers", () => {
    const patch = mapBridgeStateToWorkspace(
      { model: "qwen-7b" },
      {
        endpoints: [
          {
            instance_id: "ep-1",
            model: "qwen-7b",
            status: "ready",
            host: "localhost",
            port: 8000,
          },
        ],
        jobs: [{ id: "j1", type: "train", status: "running" }],
      },
    );
    expect(patch.currentModel).toBe("qwen-7b");
    expect(patch.endpoints?.length).toBe(1);
    expect(patch.endpoints?.[0]?.id).toBe("ep-1");
    expect(patch.jobs?.[0]?.id).toBe("j1");
    expect(patch.degradedFields).toEqual([]);
  });

  it("records degraded fields for non-object dashboard", () => {
    const patch = mapBridgeStateToWorkspace(null, "not-an-object");
    expect(patch.degradedFields).toContain("dashboard");
  });
});

describe("CompanionBridgeClient reconnect policy", () => {
  it("failPending rejects all pending without replay", async () => {
    const client = new CompanionBridgeClient();
    const pending = new Promise((resolve, reject) => {
      (client as unknown as { pending: Map<number, unknown> }).pending.set(1, {
        resolve,
        reject,
        timer: null,
        method: "project_create",
      });
    });
    client.failPending(new Error("bridge reconnect — pending request cancelled (no auto-replay)"));
    await expect(pending).rejects.toThrow(/no auto-replay/);
  });

  it("abortInFlightSend resolves in-flight send_message without rejecting", async () => {
    const client = new CompanionBridgeClient();
    const pending = new Promise<unknown>((resolve, reject) => {
      (client as unknown as { pending: Map<number, unknown> }).pending.set(1, {
        resolve,
        reject,
        timer: null,
        method: "send_message",
      });
    });
    client.abortInFlightSend();
    await expect(pending).resolves.toEqual({ aborted: true, cancelled: true });
  });
});

describe("Bun-only PROJECT_SUBCOMMANDS", () => {
  it("defines C1 and C2 subcommands without requiring Python SUBCOMMANDS", () => {
    const names = PROJECT_SUBCOMMANDS.map((s) => s.name);
    expect(names).toContain("list");
    expect(names).toContain("create");
    expect(names).toContain("delete");
    expect(names).toContain("switch");
    expect(names).toContain("import");
    expect(names).toContain("init");
    expect(names).toContain("run");
  });

  it("defines project status command", () => {
    const status = PROJECT_SUBCOMMANDS.find((s) => s.name === "status");
    expect(status?.rpc).toBe("project_status");
    expect(status?.unavailable).toBeUndefined();
  });

  it("filters project subcommands from query", () => {
    const matches = filterSlashCommands([], "project cr");
    expect(matches.some((m) => m.name.includes("create"))).toBe(true);
  });
});

describe("parseSlashLine project", () => {
  it("parses /project create demo", () => {
    expect(parseSlashLine("/project create demo")).toEqual({
      name: "project",
      args: "create demo",
    });
  });

  it("preserves quoted arguments and Windows paths", () => {
    expect(
      tokenizeSlashArgs('create demo --name "Research Project"'),
    ).toEqual(["create", "demo", "--name", "Research Project"]);
    expect(
      tokenizeSlashArgs('import demo "C:\\Research Files\\paper.pdf"'),
    ).toEqual(["import", "demo", "C:\\Research Files\\paper.pdf"]);
  });
});
