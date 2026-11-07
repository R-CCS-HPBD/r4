/**
 * /project register <id> [--name N] — put a project on the platform (server)
 * through the bridge's `project_register` (POST /gw/projects, ADR-051 D-1:
 * any signed-in user; the caller becomes its owner; safe to repeat).
 *
 * Maintainer 2026-11-03: "i was not sure [how] to register new project on
 * server". `/project create` keeps a project on this machine only; this is
 * the one step that makes loops, jobs and knowledge accept it. Never changes
 * the tab's project selection. Kept out of slash.ts (gate-frozen).
 */

import { actions, workspace } from "../stores";
import type { getCompanionBridge } from "../bridge/jsonl-client";
import type { ProjectRegisterResult } from "../bridge/rpc-types.gen";

type Bridge = ReturnType<typeof getCompanionBridge>;
type Result = { kind: "handled" } | { kind: "insert"; text: string } | { kind: "error"; message: string };

export const PROJECT_REGISTER_HELP =
  "Register a project on the server (loops, jobs, knowledge need it): /project register <id> [--name …]";

/** The value after `--name`, if any. */
function nameArg(tokens: string[]): string {
  const at = tokens.indexOf("--name");
  return at >= 0 ? (tokens[at + 1] ?? "") : "";
}

export async function handleProjectRegister(bridge: Bridge, tokens: string[]): Promise<Result> {
  const projectId = tokens[1];
  if (!projectId) return { kind: "insert", text: "/project register " };
  const result = (await bridge.request(
    "project_register",
    { project_id: projectId, name: nameArg(tokens) },
    30_000,
  )) as ProjectRegisterResult;
  if (!result.ok) return { kind: "error", message: result.message || `register failed for ${projectId}` };
  actions.sendSystemNotice(result.message);
  return { kind: "handled" };
}

/** The line after `/project create` on a connected console: a created
 * project lives on this machine until it is registered. */
export function createdNote(projectId: string, created: boolean): string {
  if (!created || workspace.connection.gateway !== "connected") return "";
  return ` — on this machine only; if the server does not know it yet: /project register ${projectId}`;
}
