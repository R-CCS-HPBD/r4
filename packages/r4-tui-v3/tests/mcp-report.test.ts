import { describe, expect, test } from "bun:test";

import { buildMcpReportLines, serverLine } from "../src/lib/mcp-report";
import { exposureNoticeTail, mcpExplanation, mcpRowKey } from "../src/components/list-overlay";

describe("mcp-report", () => {
  test("serverLine renders status icon + tool count", () => {
    expect(
      serverLine({ name: "filesystem", status: "healthy", url: "http://fs:8080" }, 3),
    ).toBe(" ✓ filesystem — 3 tools — http://fs:8080");
    expect(serverLine({ name: "db", status: "unhealthy" }, 0)).toBe(" ✗ db — 0 tools");
    expect(serverLine({ name: "fw", status: "starting" }, 1)).toBe(" ? fw — 1 tools");
  });

  test("serverLine falls back to a stable label", () => {
    expect(serverLine({ server_id: "srv-9", status: "unknown" }, 0)).toBe(
      " ? srv-9 — 0 tools",
    );
  });

  test("serverLine tags source when present (registry vs user-config)", () => {
    expect(
      serverLine({ name: "platform", status: "healthy", source: "registry" }, 5),
    ).toBe(" ✓ platform — 5 tools · registry");
    expect(
      serverLine({ name: "context7", status: "declared (not probed) · not agent-visible", source: "user-config:user", url: "https://mcp.context7.com/mcp" }, 0),
    ).toBe(" ? context7 — 0 tools — https://mcp.context7.com/mcp · user-config:user");
  });

  test("buildMcpReportLines reports empty inventory honestly", async () => {
    const bridge = {
      request: async (method: string) =>
        method === "list_mcp_servers" ? { servers: [] } : { tools: [] },
    } as never;
    const lines = await buildMcpReportLines(bridge);
    expect(lines).toEqual(["MCP: no servers registered on this gateway or declared locally"]);
  });

  test("buildMcpReportLines appends actionable footer for declared-only rows", async () => {
    const bridge = {
      request: async (method: string) => {
        if (method === "list_mcp_servers") {
          return {
            servers: [
              { name: "platform", status: "healthy", source: "registry", agent_visible: true },
              { name: "context7", status: "declared (not probed) · not agent-visible", source: "user-config:user", agent_visible: false },
            ],
          };
        }
        return { tools: [] };
      },
    } as never;
    const lines = await buildMcpReportLines(bridge);
    const joined = lines.join("\n");
    expect(joined).toContain("1 declared locally — NOT visible to the agent (context7)");
    expect(joined).toContain("r4-mcp remote-proxy --config ~/.config/r4/mcp-servers.json");
    expect(joined).toContain("r4 mcp register <name> --url http://localhost:50800/<name>");
    expect(joined).toContain("toggle entries on/off in the /mcp floating pane");
  });

  test("exposureNoticeTail names each outcome", () => {
    expect(exposureNoticeTail(undefined)).toBe("");
    expect(exposureNoticeTail({ proxy: { state: "started", port: 50800 }, registration: { state: "registered" } }))
      .toContain("registered ✓");
    expect(exposureNoticeTail({ proxy: { state: "running", port: 50800 }, registration: { state: "blocked", reason: "API error: 500" } }))
      .toContain("registration blocked: API error: 500");
    expect(exposureNoticeTail({ proxy: { state: "failed", reason: "spawn failed" } }))
      .toContain("proxy failed: spawn failed");
  });

  test("mcpExplanation prefers description, then hint, then url", () => {
    expect(mcpExplanation({ description: "docs" })).toBe("docs");
    expect(mcpExplanation({ hint: "activate me" })).toBe("activate me");
    expect(mcpExplanation({ url: "https://x/mcp" })).toBe("https://x/mcp");
    expect(mcpExplanation({})).toBeNull();
  });

  test("buildMcpReportLines counts tools per server", async () => {
    const bridge = {
      request: async (method: string) => {
        if (method === "list_mcp_servers") {
          return {
            servers: [
              { name: "filesystem", server_id: "s1", status: "healthy" },
              { name: "lab", server_id: "s2", status: "unhealthy" },
            ],
          };
        }
        if (method === "list_mcp_tools") {
          return {
            tools: [
              { name: "read_file", server: "filesystem" },
              { name: "write_file", server: "filesystem" },
              { name: "query", server: "lab" },
            ],
          };
        }
        throw new Error(`unexpected method ${method}`);
      },
    } as never;
    const lines = await buildMcpReportLines(bridge);
    expect(lines[0]).toBe("MCP servers (2):");
    expect(lines[1]).toBe(" ✓ filesystem — 2 tools");
    expect(lines[2]).toBe(" ✗ lab — 1 tools");
  });

  test("mcpRowKey keeps a local row distinct from its same-name registry row", () => {
    // Toggling ON auto-registers the name in UMRS: the refreshed list then
    // carries BOTH rows under one name. Name-keyed rows shadowed each other
    // (on/off chip "disappeared", Enter hit the read-only registry row).
    const local = { name: "fetch", source: "user-config:user", enabled: true };
    const registry = { name: "fetch", server_id: "srv-1", source: "registry" };
    expect(mcpRowKey(local)).toBe("local:fetch");
    expect(mcpRowKey(registry)).toBe("reg:srv-1");
    expect(mcpRowKey(local)).not.toBe(mcpRowKey(registry));
  });
});
