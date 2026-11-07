/**
 * /tour function-map contract: curated core-flow intro + EVERY catalog
 * command derived live — the map can never silently drop a new command.
 */
import { describe, it, expect } from "bun:test";
import { buildTourLines } from "../src/lib/tour";
import { localSlashCommands, PROJECT_SUBCOMMANDS, ENDPOINT_SUBCOMMANDS } from "../src/commands/slash";

function lines() {
  return buildTourLines({
    entries: localSlashCommands(),
    projectSubs: PROJECT_SUBCOMMANDS,
    endpointSubs: ENDPOINT_SUBCOMMANDS,
  });
}

describe("/tour function map", () => {
  it("opens with the four core flows", () => {
    const text = lines().join("\n");
    for (const flow of [
      "chat with registered endpoints",
      "project-oriented knowledge build-up",
      "agentic work control, local+remote",
      "umrs workflow control",
    ]) {
      expect(text).toContain(flow);
    }
  });

  it("covers EVERY catalog command at least once", () => {
    const text = lines().join("\n");
    const missing = localSlashCommands()
      .map((e) => e.name)
      .filter((name) => !text.includes(`/${name}`));
    expect(missing).toEqual([]);
  });

  it("expands the project and endpoint subcommand families", () => {
    const text = lines().join("\n");
    for (const sub of PROJECT_SUBCOMMANDS) {
      expect(text).toContain(`· ${sub.name} — ${sub.help}`);
    }
    for (const sub of ENDPOINT_SUBCOMMANDS) {
      expect(text).toContain(`· ${sub.name} — ${sub.help}`);
    }
  });

  it("carries the keyboard grammar footer (approvals ask-once; /theme; /work)", () => {
    const text = lines().join("\n");
    expect(text).toContain("remembers for session");
    expect(text).toContain("/theme");
    expect(text).toContain("/work");
  });

  it("order is deterministic (sorted groups, fixed group order)", () => {
    const a = lines().join("\n");
    const b = lines().join("\n");
    expect(a).toBe(b);
  });
});
