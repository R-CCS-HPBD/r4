/**
 * Mission Control “Start research” helpers — slash builders + project_list picker.
 * Dispatches via dispatchSlashCommand (no fake chat user lines).
 */

import { getCompanionBridge } from "../bridge/jsonl-client";
import { dispatchSlashCommand, tokenizeSlashArgs } from "../commands/slash";
import { isOfflineBackend } from "./console-mode";
import { actions, workspace, activeTab } from "../stores";

export type StartResearchKind = "create" | "import" | "plan";

/** Quote a token for tokenizeSlashArgs (spaces / quotes). */
export function quoteSlashArg(value: string): string {
  const s = value.trim();
  if (!s) return s;
  if (!/[\s"']/.test(s)) return s;
  // Only escape " — backslashes are literal (Windows paths); tokenizeSlashArgs only unescapes \"
  return `"${s.replace(/"/g, '\\"')}"`;
}

export function buildCreateSlashLine(projectId: string, name?: string): string {
  const id = projectId.trim();
  if (!id) return "/project create ";
  if (name?.trim()) return `/project create ${quoteSlashArg(id)} --name ${quoteSlashArg(name.trim())}`;
  return `/project create ${quoteSlashArg(id)}`;
}

export function buildImportSlashLine(projectId: string, pathsText: string): string {
  const id = projectId.trim();
  if (!id) return "/project import ";
  const raw = pathsText.trim();
  if (!raw) return `/project import ${quoteSlashArg(id)} `;
  let paths: string[];
  try {
    paths = tokenizeSlashArgs(raw);
  } catch {
    paths = raw.split(/\s+/).filter(Boolean);
  }
  if (paths.length === 0) return `/project import ${quoteSlashArg(id)} `;
  return `/project import ${quoteSlashArg(id)} ${paths.map(quoteSlashArg).join(" ")}`;
}

export function buildPlanSlashLine(projectId: string, goal: string): string {
  const id = projectId.trim();
  if (!id) return "/project plan ";
  const g = goal.trim();
  if (!g) return `/project plan ${quoteSlashArg(id)} `;
  // Goal is free text after id — do not quote the whole goal (tokenize joins remainder).
  return `/project plan ${quoteSlashArg(id)} ${g}`;
}

export function mapProjectListToPickerItems(
  projects: Array<{ project_id: string; name?: string; active?: boolean }>,
): Array<{ key: string; label: string }> {
  return projects.map((p) => ({
    key: p.project_id,
    label: `${p.active ? "* " : "  "}${p.project_id}${p.name ? ` (${p.name})` : ""}`,
  }));
}

async function noticeSlashResult(result: Awaited<ReturnType<typeof dispatchSlashCommand>>): Promise<void> {
  if (result.kind === "error") {
    actions.sendSystemNotice(result.message);
    return;
  }
  if (result.kind === "insert") {
    actions.sendSystemNotice(`Need more args — ${result.text.trim()}`);
  }
}

export async function runDispatchSlash(line: string): Promise<void> {
  const result = await dispatchSlashCommand(line);
  await noticeSlashResult(result);
}

export async function submitCreate(projectId: string, name?: string): Promise<void> {
  const id = projectId.trim();
  if (!id) {
    actions.sendSystemNotice("Enter a project id");
    return;
  }
  actions.closeStartResearch();
  await runDispatchSlash(buildCreateSlashLine(id, name));
}

export async function submitImport(pathsText: string): Promise<void> {
  const id = activeTab().project?.id;
  if (!id) {
    actions.sendSystemNotice("No project selected — New or Switch first");
    return;
  }
  if (!pathsText.trim()) {
    actions.sendSystemNotice("Paste one or more absolute paths");
    return;
  }
  actions.closeStartResearch();
  await runDispatchSlash(buildImportSlashLine(id, pathsText));
}

export async function submitPlan(goal: string): Promise<void> {
  const id = activeTab().project?.id;
  if (!id) {
    actions.sendSystemNotice("No project selected — New or Switch first");
    return;
  }
  if (!goal.trim()) {
    actions.sendSystemNotice("Enter a research goal");
    return;
  }
  actions.closeStartResearch();
  await runDispatchSlash(buildPlanSlashLine(id, goal));
}

export async function runInit(): Promise<void> {
  const id = activeTab().project?.id;
  if (!id) {
    actions.sendSystemNotice("No project selected — New or Switch first");
    return;
  }
  await runDispatchSlash(`/project init ${quoteSlashArg(id)}`);
}

export async function runReadiness(): Promise<void> {
  const id = activeTab().project?.id;
  if (!id) {
    actions.sendSystemNotice("No project selected — New or Switch first");
    return;
  }
  await runDispatchSlash(`/project readiness ${quoteSlashArg(id)}`);
}

/** Open Switch picker from project_list; offline/empty → notice + New. */
export async function openSwitchPicker(): Promise<void> {
  if (isOfflineBackend()) {
    actions.sendSystemNotice("Offline — cannot list projects. Use New or leave demo mode.");
    actions.openStartResearch("create");
    return;
  }
  try {
    const bridge = getCompanionBridge();
    const result = (await bridge.request("project_list", {})) as {
      projects?: Array<{ project_id: string; name?: string; active?: boolean }>;
    };
    const items = mapProjectListToPickerItems(result.projects ?? []);
    if (items.length === 0) {
      actions.sendSystemNotice("No projects yet — create one with New");
      actions.openStartResearch("create");
      return;
    }
    actions.openSlashPicker({
      title: "Switch project",
      commandPrefix: "/project switch ",
      items,
      mode: "slash",
    });
  } catch (err) {
    actions.sendSystemNotice(
      err instanceof Error ? err.message : `project_list failed: ${String(err)}`,
    );
    actions.openStartResearch("create");
  }
}

export function openImportOrPlan(kind: "import" | "plan"): void {
  if (!activeTab().project?.id) {
    actions.sendSystemNotice("No project selected — New or Switch first");
    return;
  }
  actions.openStartResearch(kind);
}
