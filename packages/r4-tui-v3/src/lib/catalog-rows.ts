/**
 * Wave F — catalog rows over the unified asset kinds that have client data
 * today (skill · mcp_server · code_change · session). Other kinds
 * (graph / vector_store / pipeline / dataset / execution / artifact) arrive
 * with the catalog/snapshot protocol — listed honestly as absent kinds.
 */

import type { GitChange } from "./git-changes";
import type { SessionRecord } from "../types";
import type { McpServerEntry } from "./mcp-report";
import type { UiSkill } from "../types";

export type CatalogKind = "skill" | "mcp_server" | "code_change" | "session";

export interface CatalogRow {
  kind: CatalogKind;
  /** Primary identifier (name for skills, path for changes, id for sessions). */
  id: string;
  /** One-line meta (source for skills, status for servers/changes, model for sessions). */
  meta: string;
  /** Verb the row offers (empty = display-only today). */
  verb: "toggle" | "open" | "resume" | "";
}

export const PENDING_KINDS = [
  "graph",
  "vector_store",
  "rag_pipeline",
  "dataset",
  "execution",
  "artifact",
] as const;

export function catalogRows(input: {
  skills: UiSkill[];
  mcpServers: McpServerEntry[] | null;
  changeSets: GitChange[];
  sessions: SessionRecord[];
}): CatalogRow[] {
  const rows: CatalogRow[] = [];
  for (const skill of input.skills) {
    rows.push({
      kind: "skill",
      id: skill.name,
      meta: skill.source || "builtin",
      verb: "toggle",
    });
  }
  for (const server of input.mcpServers ?? []) {
    const id = server.name ?? server.server_id ?? server.id ?? "?";
    rows.push({
      kind: "mcp_server",
      id,
      meta: server.status ?? "?",
      verb: "", // on/off awaits server-side toggle semantics
    });
  }
  for (const change of input.changeSets) {
    rows.push({
      kind: "code_change",
      id: change.path,
      meta: change.status,
      verb: "open",
    });
  }
  for (const session of input.sessions) {
    rows.push({
      kind: "session",
      id: session.id,
      meta: `${session.name} · ${session.model}`,
      verb: "resume",
    });
  }
  return rows;
}

/** Group header text with per-kind counts (catalog reads like a summary). */
export function catalogKindLabel(kind: CatalogKind): string {
  switch (kind) {
    case "skill":
      return "Skills";
    case "mcp_server":
      return "MCP servers";
    case "code_change":
      return "Code changes";
    case "session":
      return "Sessions";
  }
}
