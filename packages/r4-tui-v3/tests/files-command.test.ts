/**
 * /files + /explain command pins (TUI/rccs function sweep, 2026-09-27):
 * verbs ride the mocked bridge — params pass through, payloads and failures
 * render as notices, missing args answer with usage hints (never silent).
 */
import { describe, it, expect, beforeEach, afterEach, mock } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";

const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
let reply: unknown = {};
let failure: Error | null = null;

mock.module("../src/lib/console-mode", () => ({
  isOfflineBackend: () => false,
}));
mock.module("../src/bridge", () => ({
  getCompanionBridge: () => ({
    request: async (method: string, params: Record<string, unknown>) => {
      calls.push({ method, params });
      if (failure) throw failure;
      return reply;
    },
  }),
}));

const { handleFilesCommand, handleExplainCommand } = await import(
  "../src/commands/files-command"
);

function lastNotice(): string {
  const systems = workspace.chatMessages.filter((m) => m.role === "system");
  return systems[systems.length - 1]?.content ?? "";
}

describe("/files command", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
    reply = {};
    failure = null;
  });

  afterEach(() => resetWorkspace());

  it("upload passes path + key and renders the gateway receipt", async () => {
    reply = { ok: true, key: "demo/clips/a.mp4", bucket: "r4-data", size: 12 };
    await handleFilesCommand("upload /tmp/a.mp4 clips/a.mp4");
    expect(calls).toEqual([
      { method: "files.upload", params: { path: "/tmp/a.mp4", key: "clips/a.mp4" } },
    ]);
    expect(lastNotice()).toContain("demo/clips/a.mp4");
    expect(lastNotice()).toContain("r4-data");
  });

  it("upload without a path answers the usage hint and never calls the bridge", async () => {
    await handleFilesCommand("upload");
    expect(calls).toEqual([]);
    expect(lastNotice()).toContain("/files upload <path> [key]");
  });

  it("download requires key + dest and renders verb failures verbatim", async () => {
    await handleFilesCommand("download demo/x.bin");
    expect(calls).toEqual([]);
    expect(lastNotice()).toContain("/files download <key> <dest>");

    failure = new Error("API error: 404 not found");
    await handleFilesCommand("download demo/x.bin /tmp/x.bin");
    expect(calls[0]!.method).toBe("files.download");
    expect(lastNotice()).toContain("API error: 404 not found");
  });
});

describe("/explain command", () => {
  beforeEach(() => {
    resetWorkspace();
    calls.length = 0;
    reply = {};
    failure = null;
  });

  afterEach(() => resetWorkspace());

  it("renders cause + remediation for a known id", async () => {
    reply = {
      found: true,
      cause: "JWT rejected by the auth sidecar",
      remediation: "r4 auth login --refresh",
      exit_code: 10,
      retryable: false,
    };
    await handleExplainCommand("auth.failed");
    expect(calls).toEqual([{ method: "explain", params: { error_id: "auth.failed" } }]);
    expect(lastNotice()).toContain("cause: JWT rejected");
    expect(lastNotice()).toContain("fix: r4 auth login --refresh");
    expect(lastNotice()).toContain("exit 10");
  });

  it("unknown id answers honestly with the --list hint", async () => {
    reply = { found: false, hint: "r4 explain --list" };
    await handleExplainCommand("no.such");
    expect(lastNotice()).toContain("unknown error id");
  });

  it("empty args never calls the bridge", async () => {
    await handleExplainCommand("  ");
    expect(calls).toEqual([]);
    expect(lastNotice()).toContain("/explain auth.forbidden");
  });
});
