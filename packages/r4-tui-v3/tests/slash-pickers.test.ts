/**
 * Slash picker resolution for endpoint up/down.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";
import {
  parseDownPickerIntent,
  parseEndpointInvocation,
  parseModelsPickerIntent,
  parseUpPickerIntent,
  resolveSlashPicker,
} from "../src/commands/slash-pickers";

beforeEach(() => {
  resetWorkspace();
  actions.closeAllOverlays();
  process.env.R4_TUI_BACKEND = "offline";
});

describe("parseUpPickerIntent", () => {
  it("matches /endpoint up without a model", () => {
    expect(parseUpPickerIntent("/endpoint up")).toEqual({ requestNew: false });
  });

  it("matches /up alias", () => {
    expect(parseUpPickerIntent("/up")).toEqual({ requestNew: false });
  });

  it("matches /endpoint up --new", () => {
    expect(parseUpPickerIntent("/endpoint up --new")).toEqual({ requestNew: true });
  });

  it("skips when model is provided", () => {
    expect(parseUpPickerIntent("/endpoint up agents-a1")).toBeNull();
  });
});

describe("parseEndpointInvocation", () => {
  it("parses /endpoint list via legacy helper", () => {
    expect(parseEndpointInvocation("/endpoint list")).toEqual({
      sub: "list",
      args: [],
    });
  });
});

describe("resolveSlashPicker", () => {
  it("opens a model picker for /endpoint up (offline fallback)", async () => {
    actions.setBackendState({
      models: [
        { id: "a", name: "agents-a1", scope: "user" },
        { id: "b", name: "ai4s-smoke", scope: "user" },
      ],
    });
    const picker = await resolveSlashPicker("/endpoint up");
    expect(picker?.title).toContain("Provision endpoint");
    expect(picker?.items.length).toBe(2);
    expect(picker?.commandPrefix).toBe("/endpoint up ");
  });

  it("still opens picker with a single registry model", async () => {
    actions.setBackendState({
      models: [{ id: "a", name: "solo-model", scope: "user" }],
    });
    const picker = await resolveSlashPicker("/endpoint up");
    expect(picker?.items.length).toBe(1);
  });

  it("skips picker when model arg is present", async () => {
    actions.setBackendState({
      models: [
        { id: "a", name: "agents-a1", scope: "user" },
        { id: "b", name: "ai4s-smoke", scope: "user" },
      ],
    });
    expect(await resolveSlashPicker("/endpoint up agents-a1")).toBeNull();
  });
});

describe("parseModelsPickerIntent", () => {
  it("matches bare /models and list", () => {
    expect(parseModelsPickerIntent("/models")).toBe(true);
    expect(parseModelsPickerIntent("/model")).toBe(true);
    expect(parseModelsPickerIntent("/models list")).toBe(true);
  });

  it("skips /models default and named selection", () => {
    expect(parseModelsPickerIntent("/models default")).toBe(false);
    expect(parseModelsPickerIntent("/models kimi-k3@rikyu")).toBe(false);
  });

  it("opens set_model picker offline from workspace models", async () => {
    actions.setBackendState({
      models: [
        { id: "a", name: "kimi-k3@rikyu", scope: "user" },
        { id: "b", name: "other", scope: "user" },
      ],
    });
    const picker = await resolveSlashPicker("/models");
    expect(picker).not.toBeNull();
    expect(picker!.mode).toBe("set_model");
    expect(picker!.items.length).toBe(2);
    expect(picker!.items[0]!.modelName).toBe("kimi-k3@rikyu");
  });

  it("drops a bare inventory row when a qualified twin exists (unroutable duplicate)", async () => {
    // Regression: picking the bare `kimi-k3` (CLI inventory) instead of
    // `kimi-k3@rikyu` (bridge) sent a source-less model → "not provisioned".
    actions.setBackendState({
      models: [
        { id: "a", name: "kimi-k3", scope: "user" },
        { id: "b", name: "kimi-k3@rikyu", scope: "user" },
        { id: "c", name: "solo-other", scope: "user" },
      ],
    });
    const picker = await resolveSlashPicker("/models");
    expect(picker).not.toBeNull();
    const names = picker!.items.map((i) => i.modelName);
    expect(names).toContain("kimi-k3@rikyu");
    expect(names).toContain("solo-other");
    expect(names).not.toContain("kimi-k3");
    expect(picker!.items.length).toBe(2);
    const kimi = picker!.items.find((i) => i.modelName === "kimi-k3@rikyu")!;
    expect(kimi.group).toBe("rikyu");
  });
});

describe("parseDownPickerIntent", () => {
  it("matches /endpoint down without an id", () => {
    expect(parseDownPickerIntent("/endpoint down")).toEqual({ force: false });
  });
});

describe("dispatchSlashFromChat echo", () => {
  it("records the user slash command in chat", async () => {
    await actions.dispatchSlashFromChat("/help");
    const userLines = workspace.chatMessages.filter((m) => m.role === "user");
    expect(userLines.some((m) => m.content === "/help")).toBe(true);
  });

  it("places the user line before system output", () => {
    actions.recordUserSlashCommand("/endpoint list");
    actions.sendSystemNotice("endpoints (1 active):");
    const userIdx = workspace.chatMessages.findIndex(
      (m) => m.role === "user" && m.content === "/endpoint list",
    );
    const systemIdx = workspace.chatMessages.findIndex(
      (m) => m.role === "system" && m.content === "endpoints (1 active):",
    );
    expect(userIdx).toBeGreaterThanOrEqual(0);
    expect(systemIdx).toBeGreaterThan(userIdx);
  });

  it("retracts insert-only commands from chat", () => {
    actions.recordUserSlashCommand("/project create");
    actions.retractLastUserSlashCommand("/project create");
    expect(
      workspace.chatMessages.some((m) => m.role === "user" && m.content === "/project create"),
    ).toBe(false);
  });
});
