/**
 * Plan H-5 (2026-10-18): every Python registry command, alias and subcommand
 * typed into v3 reaches either the companion (dispatch_slash_command with the
 * line intact) or the local handler catalog.manifest.json declares for it —
 * never "Unknown slash command", never a dropped argument.
 *
 * The registry and the catalog list_slash_commands exports come from
 * tests/fixtures/python-slash-registry.json, written by
 * scripts/regen-slash-fixture.py (r4/tests/test_tui_audit_bridge.py runs its
 * --check). The manifest tests below pin the manifest to the rows slash.ts
 * builds, so a new local row or alias cannot skip the manifest.
 */
import { describe, test, expect, beforeAll, afterAll, beforeEach } from "bun:test";
import fixture from "./fixtures/python-slash-registry.json";
import {
  MANIFEST_ROWS,
  manifestRow,
  rowNames,
  stageRowName,
  stageRowNames,
  type ManifestRow,
} from "../src/commands/catalog-manifest";
import {
  canonicalizeSlashAlias,
  dispatchSlashCommand,
  localSlashCommands,
  parseSlashLine,
  resetSlashCatalogCache,
  _testOnly_stageTables,
  type SlashDispatchResult,
} from "../src/commands/slash";
import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { actions, resetWorkspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

const SAMPLE_ARG = "probe-arg";

interface Probe {
  line: string;
  name: string;
  args: string;
}

interface Outcome {
  result: SlashDispatchResult;
  sent: string[];
}

function probesFor(name: string, subcommands: string[]): Probe[] {
  const forms = ["", SAMPLE_ARG, ...subcommands.map((sub) => `${sub} ${SAMPLE_ARG}`)];
  return forms.map((args) => ({ line: args ? `/${name} ${args}` : `/${name}`, name, args }));
}

const PROBES: Probe[] = fixture.registry.flatMap((row) =>
  [row.name, ...row.aliases].flatMap((name) => probesFor(name, row.subcommands)),
);

// Every name a manifest row answers to; a row's own name wins over another
// row's alias (/teamup is its own row and a /team alias: both local).
const ROW_BY_NAME = new Map<string, ManifestRow>();
for (const row of MANIFEST_ROWS) {
  for (const name of rowNames(row)) {
    if (!ROW_BY_NAME.has(name) || name === row.name) ROW_BY_NAME.set(name, row);
  }
}

function canonical(probe: Probe): { name: string; args: string; line: string } {
  const { parsed, line } = canonicalizeSlashAlias({ name: probe.name, args: probe.args }, probe.line);
  return { ...parsed, line };
}

/** True when the manifest says this verb of this row goes to the companion. */
function forwardedByManifest(row: ManifestRow | undefined, verb: string): boolean {
  if (!row || row.owner === "forward") return true;
  if (row.owner === "bare-local-args-forward") return verb !== "";
  if (row.localVerbs) return !row.localVerbs.includes(verb);
  return (row.forwardVerbs ?? []).includes(verb);
}

function withArgs(name: string, args: string): string {
  return args ? `/${name} ${args}` : `/${name}`;
}

/** The lines that count as "intact": as typed, as canonicalized, or under
 * the companion name a row forwards to (/traces list → /trace list). */
function intactLines(probe: Probe, row: ManifestRow | undefined): string[] {
  const canon = canonical(probe);
  const names = [probe.name, canon.name, row?.name, row?.forwardAs].filter((n): n is string => Boolean(n));
  return [probe.line, canon.line, ...names.map((name) => withArgs(name, canon.args))];
}

function bareLines(probe: Probe, row: ManifestRow | undefined): string[] {
  const names = [probe.name, row?.name, row?.forwardAs].filter((n): n is string => Boolean(n));
  return names.map((name) => `/${name}`);
}

function isUnknown(result: SlashDispatchResult): boolean {
  return result.kind === "error" && result.message.startsWith("Unknown slash command");
}

/** null when the outcome is what the manifest declares, else why not. */
function reachabilityVerdict(probe: Probe, row: ManifestRow | undefined, outcome: Outcome): string | null {
  if (isUnknown(outcome.result)) return "Unknown slash command";
  const canon = canonical(probe);
  const verb = (canon.args.split(/\s+/)[0] ?? "").toLowerCase();
  if (forwardedByManifest(row, verb)) {
    const intact = intactLines(probe, row);
    return outcome.sent.some((line) => intact.includes(line))
      ? null
      : `expected the companion to get ${probe.line} intact, got ${JSON.stringify(outcome.sent)}`;
  }
  if (!probe.args || row?.ignoresArgs) return null;
  // A catalog row's localRun takes no arguments: args typed to it are lost
  // unless the manifest declares that (ignoresArgs).
  if (row?.intercept === "catalog") return "dropped the arguments: the local row takes none";
  const bare = bareLines(probe, row);
  const dropped = outcome.sent.find((line) => bare.includes(line));
  return dropped ? `dropped the arguments: sent ${dropped}` : null;
}

function rowFor(probe: Probe): ManifestRow | undefined {
  return ROW_BY_NAME.get(canonical(probe).name);
}

async function dispatchRecorded(line: string): Promise<Outcome> {
  const sent: string[] = [];
  setCompanionBridgeForTests({
    ensureStarted: async () => {},
    request: async (method: string, params: Record<string, unknown>) => {
      if (method === "list_slash_commands") return { commands: fixture.catalog };
      if (method !== "dispatch_slash_command") return {};
      sent.push(String(params.command ?? ""));
      return { handled: true, output: ["ok"] };
    },
  } as never);
  try {
    const result = await dispatchSlashCommand(line);
    // Let fire-and-forget work a handler started land on this recorder.
    await Bun.sleep(5);
    return { result, sent };
  } finally {
    setCompanionBridgeForTests(null);
  }
}

const savedBackendEnv = process.env["R4_TUI_BACKEND"];
const originalQuit = actions.requestQuit;

beforeAll(() => {
  resetSlashCatalogCache();
  // A second quit within 3 s exits the process; the probes only need to see
  // that /quit, /exit and /q reach the local row.
  actions.requestQuit = (() => {}) as typeof actions.requestQuit;
});

afterAll(() => {
  actions.requestQuit = originalQuit;
  resetSlashCatalogCache();
  if (savedBackendEnv === undefined) delete process.env["R4_TUI_BACKEND"];
  else process.env["R4_TUI_BACKEND"] = savedBackendEnv;
});

beforeEach(() => {
  delete process.env["R4_TUI_BACKEND"];
  stopBackendPoller();
  stopKnowledgePoller();
  setDemoMode(false);
  resetWorkspace();
  actions.closeAllOverlays();
});

describe("every Python registry command, alias and subcommand is reachable", () => {
  test("the fixture covers the registry", () => {
    expect(fixture.registry.length).toBeGreaterThan(50);
    expect(PROBES.length).toBeGreaterThan(200);
  });

  for (const probe of PROBES) {
    test(probe.line, async () => {
      const outcome = await dispatchRecorded(probe.line);
      expect(reachabilityVerdict(probe, rowFor(probe), outcome)).toBeNull();
    });
  }
});

function verbProbe(row: ManifestRow, args: string): Probe {
  return { line: withArgs(row.name, args), name: row.name, args };
}

/** Sent to the companion intact (under the row's name or forwardAs). */
async function forwardsIntact(probe: Probe, row: ManifestRow): Promise<boolean> {
  const outcome = await dispatchRecorded(probe.line);
  return outcome.sent.some((line) => intactLines(probe, row).includes(line));
}

function forwardVerbTests(row: ManifestRow): void {
  for (const verb of row.forwardVerbs ?? []) {
    const probe = verbProbe(row, verb ? `${verb} ${SAMPLE_ARG}` : "");
    test(`${probe.line} goes to the companion intact`, async () => {
      expect(await forwardsIntact(probe, row)).toBe(true);
    });
  }
}

function localVerbTests(row: ManifestRow): void {
  const other = verbProbe(row, `probe-verb ${SAMPLE_ARG}`);
  test(`${other.line} (a verb not in localVerbs) goes to the companion intact`, async () => {
    expect(await forwardsIntact(other, row)).toBe(true);
  });
  for (const verb of row.localVerbs ?? []) {
    // "on" makes write-plan a complete local verb; the others ignore it.
    const probe = verbProbe(row, verb ? `${verb} on` : "");
    test(`${probe.line} stays in v3`, async () => {
      expect(await forwardsIntact(probe, row)).toBe(false);
    });
  }
}

describe("the manifest's verb sets decide local vs forwarded", () => {
  for (const row of MANIFEST_ROWS.filter((r) => r.forwardVerbs)) forwardVerbTests(row);
  for (const row of MANIFEST_ROWS.filter((r) => r.localVerbs)) localVerbTests(row);
});

describe("the verdict catches the failure classes", () => {
  const probe = (line: string): Probe => {
    const parsed = parseSlashLine(line)!;
    return { line, name: parsed.name, args: parsed.args };
  };
  const handled: SlashDispatchResult = { kind: "handled" };
  const localRow: ManifestRow = { name: "x-local", owner: "local", aliases: [], intercept: "catalog" };

  test("an unknown command", () => {
    const unknown: SlashDispatchResult = { kind: "error", message: "Unknown slash command: /memory" };
    expect(reachabilityVerdict(probe("/memory"), undefined, { result: unknown, sent: [] })).toBe(
      "Unknown slash command",
    );
  });

  test("a companion command a local row swallowed", () => {
    expect(reachabilityVerdict(probe("/memory list"), undefined, { result: handled, sent: [] })).toContain(
      "intact",
    );
  });

  test("a forwarded line that lost its arguments", () => {
    const outcome = { result: handled, sent: ["/memory"] };
    expect(reachabilityVerdict(probe("/memory list"), undefined, outcome)).toContain("intact");
    const bareLocal = { ...localRow, owner: "bare-local-args-forward" as const };
    expect(reachabilityVerdict(probe("/x-local on"), bareLocal, { result: handled, sent: ["/x-local"] })).toContain(
      "intact",
    );
  });

  test("a local row that forwards the bare command", () => {
    const outcome = { result: handled, sent: ["/x-local"] };
    expect(reachabilityVerdict(probe("/x-local y"), localRow, outcome)).toContain("dropped");
    expect(reachabilityVerdict(probe("/x-local y"), { ...localRow, ignoresArgs: true }, outcome)).toBeNull();
  });

  test("intact forwards pass, under the canonical or forwardAs name too", () => {
    const traces = manifestRow("traces");
    expect(reachabilityVerdict(probe("/traces list 7"), traces, { result: handled, sent: ["/trace list 7"] })).toBeNull();
    const knowledge = manifestRow("knowledge");
    expect(reachabilityVerdict(probe("/kb mof"), knowledge, { result: handled, sent: ["/knowledge mof"] })).toBeNull();
  });
});

describe("catalog.manifest.json is the v3 rows' ownership", () => {
  const OWNERS = new Set(["local", "forward", "bare-local-args-forward"]);
  const STAGES = new Set(["wave", "builtin", "owned", "pane", "managed", "catalog"]);
  const slashRows = MANIFEST_ROWS.filter((row) => row.slashRow !== false);

  test("names are unique and fields are well-formed", () => {
    expect(new Set(MANIFEST_ROWS.map((row) => row.name)).size).toBe(MANIFEST_ROWS.length);
    for (const row of MANIFEST_ROWS) {
      expect(OWNERS.has(row.owner)).toBe(true);
      expect(STAGES.has(row.intercept)).toBe(true);
      expect(Boolean(row.forwardVerbs && row.localVerbs)).toBe(false);
      if (row.forwardVerbs || row.localVerbs) expect(row.owner).toBe("local");
      for (const [alias, prefix] of Object.entries(row.canon ?? {})) {
        expect(row.aliases).toContain(alias);
        expect(typeof prefix).toBe("string");
      }
      // A catalog row's localRun gets no arguments: the drop is declared.
      expect(Boolean(row.ignoresArgs)).toBe(row.owner === "local" && row.intercept === "catalog");
    }
  });

  test("every v3 catalog row has a manifest row, and only those", () => {
    const local = localSlashCommands().map((entry) => entry.name).sort();
    expect(local).toEqual(slashRows.map((row) => row.name).sort());
  });

  test("bareOnly and aliases come from the manifest", () => {
    for (const entry of localSlashCommands()) {
      const row = manifestRow(entry.name)!;
      expect(Boolean(entry.bareOnly)).toBe(row.owner === "bare-local-args-forward");
      expect(entry.aliases ?? []).toEqual(row.aliases);
    }
  });

  test("canon aliases rewrite to their row", () => {
    for (const row of MANIFEST_ROWS) {
      for (const alias of Object.keys(row.canon ?? {})) {
        expect(canonicalizeSlashAlias({ name: alias, args: "" }, `/${alias}`).parsed.name).toBe(row.name);
      }
    }
  });

  test("the name-keyed dispatch stages serve exactly the manifest's rows", () => {
    const tables = _testOnly_stageTables();
    for (const stage of ["builtin", "owned", "managed"] as const) {
      expect([...tables[stage]].sort()).toEqual(stageRowNames(stage).sort());
    }
  });

  test("a stage claims a row's aliases, and only its own stage's names", () => {
    expect(stageRowName("guide", "builtin")).toBe("tour");
    expect(stageRowName("coding", "builtin")).toBe("code");
    expect(stageRowName("decision", "builtin")).toBe("decision-model");
    expect(stageRowName("teamup", "builtin")).toBeUndefined();
    expect(stageRowName("constructor", "builtin")).toBeUndefined();
    expect(stageRowName("workflow", "managed")).toBeUndefined();
  });

  test("the companion leaves out the rows v3 owns as surface chrome", () => {
    const exported = new Set(fixture.catalog.map((entry) => entry.name));
    const omitted = MANIFEST_ROWS.filter((row) => row.companionRow === "omit").map((row) => row.name);
    expect(omitted.length).toBeGreaterThan(0);
    for (const name of omitted) expect(exported.has(name)).toBe(false);
  });
});
