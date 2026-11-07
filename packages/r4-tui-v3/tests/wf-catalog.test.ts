/**
 * Wave F — catalog rows + pending kinds honesty.
 */

import { describe, expect, test } from "bun:test";
import { catalogRows, catalogKindLabel, PENDING_KINDS } from "../src/lib/catalog-rows";

describe("catalogRows", () => {
  test("rows group by kind with verbs per kind", () => {
    const rows = catalogRows({
      skills: [{ name: "umrs", source: "builtin", hasMcp: true, description: "UMRS ops" }],
      mcpServers: [{ name: "platform", status: "healthy" }],
      changeSets: [{ path: "src/a.ts", status: "M" }],
      sessions: [], // give an empty store → no session rows
    });
    const byKind = Object.fromEntries(rows.map((r) => [r.kind, r]));
    expect(byKind.skill!.verb).toBe("toggle");
    expect(byKind.mcp_server!.verb).toBe(""); // server toggle pending
    expect(byKind.code_change!.verb).toBe("open");
    expect(rows.some((r) => r.kind === "session")).toBe(false);
  });

  test("absent local state reports only pending kinds in labels", () => {
    const rows = catalogRows({ skills: [], mcpServers: null, changeSets: [], sessions: [] });
    expect(rows).toEqual([]);
    expect(PENDING_KINDS).toContain("execution");
    expect(PENDING_KINDS.length).toBe(6);
  });

  test("mcp server missing name falls to server_id then id", () => {
    const rows = catalogRows({
      skills: [],
      mcpServers: [{ server_id: "srv-1", status: "down" }],
      changeSets: [],
      sessions: [],
    });
    expect(rows[0]!.id).toBe("srv-1");
  });

  test("kind labels are human text", () => {
    expect(catalogKindLabel("skill")).toBe("Skills");
    expect(catalogKindLabel("code_change")).toBe("Code changes");
  });
});
