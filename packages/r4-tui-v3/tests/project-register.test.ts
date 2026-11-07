/**
 * /project register (2026-11-03). Maintainer: "i was not sure [how] to
 * register new project on server" — /project create only wrote this
 * machine's registry and no console verb reached POST /gw/projects.
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { dispatchSlashCommand, PROJECT_SUBCOMMANDS } from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, resetWorkspace, setWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { hintForError } from "../src/lib/error-hints";

type Call = { method: string; params: Record<string, unknown> };

function fakeBridge(answers: Record<string, unknown>): Call[] {
  const calls: Call[] = [];
  setCompanionBridgeForTests({
    // /project dispatch wires its job-event channel once per bridge.
    on: () => undefined,
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      return answers[method] ?? {};
    },
  } as never);
  return calls;
}

const lastNotice = () => workspace.chatMessages.filter((m) => m.role === "system").at(-1)?.content ?? "";

beforeEach(() => {
  delete process.env.R4_TUI_BACKEND;
  setDemoMode(false);
  resetWorkspace();
  actions.closeAllOverlays();
});

afterEach(() => setCompanionBridgeForTests(null));

describe("/project register", () => {
  it("is listed with words that say what it is for", () => {
    const row = PROJECT_SUBCOMMANDS.find((s) => s.name === "register");
    expect(row?.rpc).toBe("project_register");
    expect(row?.help).toContain("on the server");
  });

  it("calls project_register and shows the bridge's success line", async () => {
    const calls = fakeBridge({
      project_register: {
        ok: true,
        project_id: "mof5",
        message: "✓ project mof5 is registered on https://gw.example (owner: alice)",
        gateway_url: "https://gw.example",
        local: "added",
      },
    });
    const result = await dispatchSlashCommand("/project register mof5 --name MOF");
    expect(result).toEqual({ kind: "handled" });
    expect(calls).toEqual([{ method: "project_register", params: { project_id: "mof5", name: "MOF" } }]);
    expect(lastNotice()).toContain("registered on https://gw.example");
  });

  it("a refusal is an error with the bridge's words", async () => {
    fakeBridge({
      project_register: { ok: false, project_id: "mof5", message: "project id 'mof5' is already taken", gateway_url: "", local: "" },
    });
    expect(await dispatchSlashCommand("/project register mof5")).toEqual({
      kind: "error",
      message: "project id 'mof5' is already taken",
    });
  });

  it("without an id it leaves the command in the input", async () => {
    const calls = fakeBridge({});
    expect(await dispatchSlashCommand("/project register")).toEqual({ kind: "insert", text: "/project register " });
    expect(calls).toEqual([]);
  });

  it("/project create on a connected console names the register step", async () => {
    setWorkspace("connection", "gateway", "connected");
    fakeBridge({ project_create: { ok: true, project_id: "mof5", created: true, selected: true } });
    await dispatchSlashCommand("/project create mof5");
    expect(lastNotice()).toContain("Created and selected project: mof5");
    expect(lastNotice()).toContain("/project register mof5");
  });

  it("the gateway's project_not_member refusal points at /project register", () => {
    expect(hintForError("403 project_not_member: not a member of project 'mof5'")).toContain("/project register");
    expect(hintForError("403 forbidden")).toContain("permission denied");
  });
});
