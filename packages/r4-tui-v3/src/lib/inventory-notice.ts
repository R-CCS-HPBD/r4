/**
 * Detail-notice text for selectable inventory rows (Settings F7).
 * Pure helpers — tests assert exact strings.
 */

import type { UiSkill } from "../types";
import type { McpServerEntry } from "./mcp-report";

export function skillNoticeText(skill: UiSkill): string {
  const mcpTag = skill.hasMcp ? " · +mcp" : "";
  const desc = skill.description?.trim();
  return (
    `skill ${skill.name} [${skill.source}]${mcpTag}` + (desc ? ` — ${desc}` : "")
  );
}

export function mcpServerNoticeText(server: McpServerEntry): string {
  const name = server.name ?? server.server_id ?? "(unnamed)";
  const status = server.status ?? "unknown";
  const source = server.source ? ` · ${server.source}` : "";
  const url = server.url ? ` — ${server.url}` : "";
  const desc = server.description ? ` (${server.description})` : "";
  const hint = server.hint ? `\n  → ${server.hint}` : "";
  return `mcp ${name} [${status}${source}]${url}${desc}${hint}`;
}
