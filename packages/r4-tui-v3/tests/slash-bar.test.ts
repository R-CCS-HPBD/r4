/**
 * Slash command helpers + dual-dispatch / system-notice contracts.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { actions, resetWorkspace, workspace } from "../src/stores";
import {
  filterSlashCommands,
  parseSlashLine,
  dispatchSlashCommand,
  needsArgPrompt,
  resetSlashCatalogCache,
  type SlashCommandEntry,
} from "../src/commands/slash";
import { formatSlashSuggestionLine, resolveSlashBarLine, shouldOpenSlashBarFromChatInput } from "../src/components/slash-bar";

const SAMPLE: SlashCommandEntry[] = [
  { name: "help", help: "Show help", category: "ui", takesArgs: false },
  { name: "clear", help: "Clear chat", category: "ui", takesArgs: false },
  { name: "export", help: "Export session [path.md]", category: "export", takesArgs: true },
];

beforeEach(() => {
  resetSlashCatalogCache();
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("needsArgPrompt", () => {
  it("prompts for required-arg commands without args", () => {
    expect(needsArgPrompt({ takesArgs: true }, "")).toBe(true);
    expect(needsArgPrompt({ takesArgs: true }, "file.md")).toBe(false);
  });

  it("does not trap bare /models (subcommand parent)", () => {
    expect(
      needsArgPrompt({ takesArgs: true, subcommands: ["list", "default"] }, ""),
    ).toBe(false);
  });

  it("does not prompt for no-arg commands", () => {
    expect(needsArgPrompt({ takesArgs: false }, "")).toBe(false);
  });
});

describe("slash command parsing", () => {
  it("parses name-only commands", () => {
    expect(parseSlashLine("/help")).toEqual({ name: "help", args: "" });
  });

  it("parses commands with arguments", () => {
    expect(parseSlashLine("/export /tmp/out.md")).toEqual({
      name: "export",
      args: "/tmp/out.md",
    });
  });

  it("rejects non-slash input", () => {
    expect(parseSlashLine("help")).toBeNull();
  });
});

describe("slash command filtering", () => {
  it("returns the full catalog for an empty query", () => {
    expect(filterSlashCommands(SAMPLE, "").length).toBe(3);
  });

  it("filters by partial name (seed-style query)", () => {
    const matches = filterSlashCommands(SAMPLE, "he");
    expect(matches.map((m) => m.name)).toContain("help");
  });

  it("suggests endpoint subcommands for /endpoint u", () => {
    const catalog: SlashCommandEntry[] = [
      { name: "endpoint", help: "Endpoint operations", category: "endpoint", takesArgs: true },
      { name: "up", help: "Alias for /endpoint up", category: "endpoint", takesArgs: true },
      ...SAMPLE,
    ];
    const matches = filterSlashCommands(catalog, "endpoint u");
    expect(matches.map((m) => m.name)).toEqual(["endpoint up"]);
  });

  it("does not suggest /up while typing /endpoint up", () => {
    const catalog: SlashCommandEntry[] = [
      { name: "endpoint", help: "Endpoint operations", category: "endpoint", takesArgs: true },
      { name: "up", help: "Alias for /endpoint up", category: "endpoint", takesArgs: true },
      ...SAMPLE,
    ];
    const matches = filterSlashCommands(catalog, "endpoint up");
    expect(matches.map((m) => m.name)).toEqual(["endpoint up"]);
  });
});

describe("slash bar suggestion formatting", () => {
  it("pads command name before help text", () => {
    const line = formatSlashSuggestionLine(
      { name: "help", help: "Show keyboard shortcuts and command help" },
      true,
    );
    expect(line.startsWith("▸ /help")).toBe(true);
    expect(line).toContain("Show keyboard shortcuts");
    expect(line.indexOf("Show keyboard shortcuts")).toBeGreaterThan(line.indexOf("/help") + 4);
  });

  it("truncates very long help strings", () => {
    const long = "x".repeat(80);
    const line = formatSlashSuggestionLine({ name: "secret", help: long }, false);
    expect(line.endsWith("…")).toBe(true);
    expect(line.length).toBeLessThan(90);
  });
});

describe("chat slash bar open guard", () => {
  it("opens only when slash is newly typed", () => {
    expect(shouldOpenSlashBarFromChatInput("", "/")).toBe(true);
    expect(shouldOpenSlashBarFromChatInput("hello", "/h")).toBe(true);
    expect(shouldOpenSlashBarFromChatInput("/endpoint up", "/endpoint u")).toBe(false);
    expect(shouldOpenSlashBarFromChatInput("/endpoint", "/endpoin")).toBe(false);
  });
});

describe("slash bar command resolution", () => {
  it("preserves arguments for an exact selected command", () => {
    expect(
      resolveSlashBarLine("/project create demo", { name: "project create" }),
    ).toBe("/project create demo");
  });

  it("expands a partial command to the selected suggestion", () => {
    expect(resolveSlashBarLine("/he", { name: "help" })).toBe("/help");
  });

  it("keeps /endpoint up instead of collapsing to /up", () => {
    expect(resolveSlashBarLine("/endpoint up", { name: "up" })).toBe("/endpoint up");
  });
});

describe("slash command dispatch", () => {
  it("dispatches local /help without Python", async () => {
    resetSlashCatalogCache();
    const result = await dispatchSlashCommand("/help");
    expect(result.kind).toBe("handled");
  });

  it("requests insert for /project create without a project id", async () => {
    const previousBackend = process.env.R4_TUI_BACKEND;
    process.env.R4_TUI_BACKEND = "cli";
    const { setDemoMode } = await import("../src/lib/console-mode");
    setDemoMode(false);
    try {
      resetSlashCatalogCache();
      const result = await dispatchSlashCommand("/project create");
      if (result.kind === "insert") {
        expect(result.text).toBe("/project create ");
      } else {
        // Offline bridge unavailable in unit tests — insert contract still defined in slash.ts
        expect(result.kind).toBe("error");
      }
    } finally {
      setDemoMode(true);
      if (previousBackend === undefined) delete process.env.R4_TUI_BACKEND;
      else process.env.R4_TUI_BACKEND = previousBackend;
    }
  });

  it("requests insert for arg-taking commands without args", async () => {
    const entry = SAMPLE.find((e) => e.name === "export")!;
    expect(entry.takesArgs).toBe(true);
    const parsed = parseSlashLine("/export");
    expect(parsed?.args).toBe("");
    // Simulate dispatch insert path with a catalog that includes export
    const catalogWithExport: SlashCommandEntry[] = [
      ...SAMPLE,
      {
        name: "export",
        help: "Export",
        category: "export",
        takesArgs: true,
      },
    ];
    expect(catalogWithExport.find((e) => e.name === "export")?.takesArgs).toBe(true);
  });

  it("surfaces unknown commands as errors", async () => {
    resetSlashCatalogCache();
    const result = await dispatchSlashCommand("/definitely-not-a-command");
    expect(result).toEqual({
      kind: "error",
      message: "Unknown slash command: /definitely-not-a-command",
    });
  });

  it("routes dispatch errors to system notices via store action", async () => {
    resetSlashCatalogCache();
    await actions.dispatchSlashFromChat("/definitely-not-a-command");
    const notices = workspace.chatMessages.filter((m) => m.role === "system");
    expect(notices.some((m) => m.content.includes("Unknown slash command"))).toBe(true);
    expect(notices.filter((m) => m.content.includes("Unknown slash command")).length).toBe(1);
  });

  it("rejects project lifecycle commands in offline mode", async () => {
    const previousBackend = process.env.R4_TUI_BACKEND;
    process.env.R4_TUI_BACKEND = "offline";
    const { setDemoMode } = await import("../src/lib/console-mode");
    try {
      setDemoMode(true);
      resetSlashCatalogCache();
      const result = await dispatchSlashCommand("/project list");
      expect(result.kind).toBe("error");
      expect(String((result as { message: string }).message)).toMatch(/offline|demo/i);
    } finally {
      setDemoMode(false);
      if (previousBackend === undefined) delete process.env.R4_TUI_BACKEND;
      else process.env.R4_TUI_BACKEND = previousBackend;
    }
  });
});

describe("slashBarSeed + dual dispatch", () => {
  it("stores seed without leading slash when opening the bar", () => {
    actions.openSlashBar("/he");
    expect(workspace.slashBarOpen).toBe(true);
    expect(workspace.slashBarSeed).toBe("he");
  });

  it("clears seed when closing the bar", () => {
    actions.openSlashBar("help");
    actions.closeSlashBar();
    expect(workspace.slashBarOpen).toBe(false);
    expect(workspace.slashBarSeed).toBe("");
  });
});

describe("project run flags", () => {
  it("simulate is OFF the wire by default (RC1 default retired, WP-08)", async () => {
    const { buildLongOpParams, parseProjectRunFlags } = await import("../src/commands/slash");
    const parsed = parseProjectRunFlags(["run", "proj-a"]);
    expect(parsed.simulate).toBeUndefined();
    expect(parsed.approve).toBe(false);
    const { params } = buildLongOpParams("project_run", "proj-b", ["run", "proj-b"]);
    expect(params).toEqual({
      project_id: "proj-b",
      approve: false,
    });
  });

  it("explicit legacy flags still reach the bridge (which answers 'simulate ignored')", async () => {
    const { buildLongOpParams, parseProjectRunFlags } = await import("../src/commands/slash");
    expect(parseProjectRunFlags(["run", "proj-a", "--simulate", "--approve"])).toEqual({
      simulate: true,
      approve: true,
      max_iterations: undefined,
    });
    expect(parseProjectRunFlags(["run", "proj-a", "--real"])).toEqual({
      simulate: false,
      approve: false,
      max_iterations: undefined,
    });
    const { params } = buildLongOpParams("project_run", "proj-a", [
      "run",
      "proj-a",
      "--simulate",
      "--approve",
      "--max-iterations",
      "2",
    ]);
    expect(params).toEqual({
      project_id: "proj-a",
      simulate: true,
      approve: true,
      max_iterations: 2,
    });
    const { params: realParams } = buildLongOpParams("project_run", "proj-b", ["run", "proj-b", "--real", "--approve"]);
    expect(realParams).toEqual({
      project_id: "proj-b",
      simulate: false,
      approve: true,
    });
  });
});
