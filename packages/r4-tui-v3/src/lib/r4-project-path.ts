/**
 * Resolve on-disk R4 project workspace paths (~/.config/r4/projects-data).
 *
 * K1.3 (2026-10-24): the Knowledge pane no longer reads the local
 * artifact_manifest.json (a durable import never wrote it); it lists the
 * server's `/k2/artifacts` (adapters/r4-cli.ts).
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export function r4ConfigDir(): string {
  const xdg = process.env["XDG_CONFIG_HOME"];
  if (xdg) return path.join(xdg, "r4");
  return path.join(os.homedir(), ".config", "r4");
}

export function projectDataDir(projectId: string): string {
  return path.join(r4ConfigDir(), "projects-data", projectId);
}

export function readResearchPlanFile(projectId: string): Record<string, unknown> | null {
  const file = path.join(projectDataDir(projectId), "research_plan.json");
  if (!fs.existsSync(file)) return null;
  try {
    const data = JSON.parse(fs.readFileSync(file, "utf-8")) as unknown;
    return data && typeof data === "object" ? (data as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
