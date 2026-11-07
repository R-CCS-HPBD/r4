/**
 * Slash subcommand hints (D-next).
 *
 * /acquisition surfaced the gap: a takesArgs command selected from the
 * slash bar showed no hint of its sub-verbs (open|off|extend), and typing
 * "/acquisition extend" left an empty suggestion list with zero guidance.
 * Generalized grammar: entries carry `subs` rows + an `argsHint` usage
 * string; exact first-token matches produce sub rows like /project and
 * /endpoint always had, and a hint line explains the args while typing.
 */
import { describe, test, expect } from "bun:test";
import {
  argHintLineForInput,
  filterSlashCommands,
  type SlashCommandEntry,
} from "../src/commands/slash";

const CATALOG: SlashCommandEntry[] = [
  {
    name: "acquisition",
    aliases: ["acquire"],
    help: "Knowledge write mode (session): open|off|extend <graph>",
    category: "companion",
    takesArgs: true,
    argsHint: "[open|off|extend <graph>[,<g2>…]]",
    subcommands: ["open", "off", "extend"],
    subs: [
      { name: "open", help: "Allow knowledge writes (approvals still gate each write)" },
      { name: "off", help: "Forbid all knowledge writes this session" },
      {
        name: "extend",
        help: "Extend ONLY the named pre-existing graph(s): extend <graph>[,<g2>…]",
        takesArgs: true,
      },
    ],
  },
  {
    name: "code",
    aliases: ["coding"],
    help: "Coding mode — toggle the builtin agentic code skill (on|off)",
    category: "companion",
    takesArgs: true,
    argsHint: "[on|off]",
    subs: [
      { name: "on", help: "Enable coding mode" },
      { name: "off", help: "Disable coding mode" },
    ],
  },
  {
    name: "endpoint",
    help: "Endpoint operations",
    category: "endpoint",
    takesArgs: true,
  },
  { name: "login", help: "Authenticate with JWT: /login <token>", category: "endpoint", takesArgs: true, argsHint: "<JWT-token>" },
  { name: "health", help: "Platform health", category: "endpoint", takesArgs: false },
];

describe("generalized sub rows", () => {
  test("exact parent name lists its subs", () => {
    const rows = filterSlashCommands(CATALOG, "acquisition");
    expect(rows.map((r) => r.name)).toEqual([
      "acquisition open",
      "acquisition off",
      "acquisition extend",
    ]);
  });

  test("subQ prefix narrows the rows", () => {
    const rows = filterSlashCommands(CATALOG, "acquisition e");
    expect(rows.map((r) => r.name)).toEqual(["acquisition extend"]);
  });

  test("alias resolves to the canonical parent for row names", () => {
    const rows = filterSlashCommands(CATALOG, "acquire o");
    expect(rows.map((r) => r.name)).toEqual(["acquisition open", "acquisition off"]);
  });

  test("sub rows carry the sub help and arg flag", () => {
    const rows = filterSlashCommands(CATALOG, "acquisition extend");
    expect(rows[0]!.help).toContain("Extend ONLY");
    expect(rows[0]!.takesArgs).toBe(true);
  });

  test("commands without subs keep fuzzy behavior (subs never hijack)", () => {
    const rows = filterSlashCommands(CATALOG, "hea").map((r) => r.name);
    expect(rows).toContain("health");
    expect(rows).not.toContain("acquisition open");
    expect(rows).not.toContain("code on");
  });
});

describe("argHintLineForInput", () => {
  test("no hint while fuzzy-searching with live suggestions", () => {
    expect(argHintLineForInput(CATALOG, "/acqui", 3)).toBeNull();
  });

  test("hint appears once the name is committed with a trailing space", () => {
    const line = argHintLineForInput(CATALOG, "/acquisition ", 3);
    expect(line).toContain("/acquisition [open|off|extend <graph>[,<g2>…]]");
    expect(line).toContain("Knowledge write mode");
  });

  test("hint stays while args are typed", () => {
    expect(argHintLineForInput(CATALOG, "/acquisition extend", 1)).toContain(
      "open|off|extend",
    );
    expect(argHintLineForInput(CATALOG, "/login eyJ", 0)).toContain("<JWT-token>");
  });

  test("endpoint hint derives from the client-known sub table", () => {
    const line = argHintLineForInput(CATALOG, "/endpoint ", 5);
    expect(line).toContain("/endpoint <list|up|down|status|help>");
  });

  test("unknown command and no-arg command produce no hint", () => {
    expect(argHintLineForInput(CATALOG, "/nope ", 0)).toBeNull();
    expect(argHintLineForInput(CATALOG, "/health ", 0)).toBeNull();
  });

  test("alias input renders the canonical command name", () => {
    const line = argHintLineForInput(CATALOG, "/acquire ", 3);
    expect(line).toContain("/acquisition [");
  });
});
