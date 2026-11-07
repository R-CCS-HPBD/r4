/**
 * MCP server + tools summary for the /mcp slash command.
 *
 * Reads through the companion bridge (list_mcp_servers / list_mcp_tools —
 * mirrored from the ACP mcp/* surface). Offline/demo backends short-circuit
 * with an honest note instead of a fake empty list.
 */

import type { getCompanionBridge } from "../bridge";

export interface McpServerEntry {
  name?: string;
  server_id?: string;
  id?: string;
  url?: string;
  status?: string;
  description?: string;
  /** "registry" (UMRS, agent-visible, health-tracked) or "user-config:<src>" (declared only). */
  source?: string;
  /** "http" | "stdio" for user-config rows. */
  type?: string;
  /** False for user-config rows: the agent cannot see them until bridged + registered. */
  agent_visible?: boolean;
  /** User-config rows carry the bridge/register hint. */
  hint?: string;
  /** Local config flag (toggled from the /mcp floating pane). Absent = on. */
  enabled?: boolean;
}

export interface McpToolEntry {
  name?: string;
  description?: string;
  server?: string;
}

type Bridge = ReturnType<typeof getCompanionBridge>;

const HEALTHY_STATUSES: ReadonlySet<string> = new Set(["healthy", "ok", "up"]);
const UNHEALTHY_STATUSES: ReadonlySet<string> = new Set(["unhealthy", "down", "error"]);

function statusIcon(status: string): string {
  const tone = status.toLowerCase();
  if (HEALTHY_STATUSES.has(tone)) return "✓";
  if (UNHEALTHY_STATUSES.has(tone)) return "✗";
  return "?";
}

/** Lines for one server row: status icon + name + tool count (+ source tag). */
export function serverLine(server: McpServerEntry, toolCount: number): string {
  const icon = statusIcon(String(server.status ?? "unknown"));
  const name = server.name ?? server.server_id ?? "(unnamed)";
  const urlSuffix = server.url ? ` — ${server.url}` : "";
  const sourceSuffix = server.source ? ` · ${server.source}` : "";
  return ` ${icon} ${name} — ${toolCount} tools${urlSuffix}${sourceSuffix}`;
}

/** Footer lines telling the user how to make declared-only servers usable. */
export function userConfigGuidanceLines(declared: McpServerEntry[]): string[] {
  if (declared.length === 0) return [];
  const names = declared.map((s) => s.name ?? s.server_id ?? "(unnamed)").join(", ");
  return [
    `${declared.length} declared locally — NOT visible to the agent (${names}). To use them:`,
    "  1. r4-mcp remote-proxy --config ~/.config/r4/mcp-servers.json --host 0.0.0.0 --port 50800",
    "     (run it on the UMRS host — registered URLs resolve server-side)",
    "  2. r4 mcp register <name> --url http://localhost:50800/<name>",
    "  3. tools then appear as mcp.<name>.<tool> in chat; health via `r4 mcp list`",
    "  toggle entries on/off in the /mcp floating pane (`enabled` flag in the config file)",
  ];
}

/** Build the whole report; throws when the bridge is unreachable (caller reports). */
export async function buildMcpReportLines(bridge: Bridge): Promise<string[]> {
  const serversRes = (await bridge.request("list_mcp_servers", {}, 15_000)) as {
    servers?: McpServerEntry[];
  };
  const servers = serversRes.servers ?? [];
  if (servers.length === 0) return ["MCP: no servers registered on this gateway or declared locally"];

  const toolsRes = (await bridge.request("list_mcp_tools", {}, 15_000)) as {
    tools?: McpToolEntry[];
  };
  const tools = toolsRes.tools ?? [];
  const countByServer = new Map<string, number>();
  for (const tool of tools) {
    const key = tool.server ?? "";
    countByServer.set(key, (countByServer.get(key) ?? 0) + 1);
  }

  const lines = [`MCP servers (${servers.length}):`];
  for (const server of servers) {
    lines.push(serverLine(server, countByServer.get(server.name ?? "") ?? 0));
  }
  lines.push(...userConfigGuidanceLines(servers.filter((s) => s.agent_visible === false)));
  return lines;
}
