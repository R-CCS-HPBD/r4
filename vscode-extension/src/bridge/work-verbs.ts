/**
 * The console's Work and Infrastructure panes as output cards (ADR-103
 * VP-4c). `/work` (`/workflows`) gathers the pane's sections in one card —
 * the workflow catalog, plan runs, teamup sessions, research loops, jobs and
 * endpoints — each from its own bridge read, so one failing section never
 * hides the others. `/work show <wf>` is the definition card (the console's
 * `workflowCardRows`), `/work run <wf>` runs it or, when values are needed,
 * drafts the console's trigger line into the composer. `/infra` is the
 * backend snapshot (`get_backend_dashboard`). No vscode import.
 */

import { cardTriggerCommand, workflowCardRows } from "../../../packages/r4-tui-v3/src/lib/workflow-card-rows";
import type { WorkflowDefinitionDetail } from "../../../packages/r4-tui-v3/src/lib/workflow-def-types";
import { planRowLabel, type TeamRunRow } from "../../../packages/r4-tui-v3/src/lib/plan-live";
import { loopsListText } from "../../../packages/r4-tui-v3/src/lib/ops-text";
import { teamListText } from "../../../packages/r4-tui-v3/src/lib/teamup-text";

export type OutputLine = { kind: "info" | "error"; text: string };

export interface WorkDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  output(command: string, lines: OutputLine[]): void;
  /** Puts a line in the composer (a trigger line that needs values). */
  draft(text: string): void;
}

type Row = Record<string, unknown>;

const CARD_WIDTH = 100;

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : value == null ? fallback : String(value);
}

function rows(value: unknown): Row[] {
  return Array.isArray(value) ? (value as Row[]) : [];
}

function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function words(args: string): string[] {
  return args.split("\t").join(" ").split(" ").filter(Boolean);
}

/** The workflow catalog section (`workflow.list`). */
export function catalogText(result: Row): string {
  const list = rows(result.workflows);
  if (!list.length) return "workflows: none registered for this project";
  const lines = list.map((w) => {
    const name = str(w.workflow_name) || str(w.name);
    const shared = w.shared ? " (shared)" : "";
    const what = w.description ? " — " + str(w.description).split("\n")[0]!.slice(0, 80) : "";
    return "  △ " + name + shared + what;
  });
  return "workflows (" + list.length + ") — /work show <name> · /work run <name>:\n" + lines.join("\n");
}

/** Plan runs (`team.runs`), with the console's row label. */
export function planRunsText(result: Row): string {
  const list = rows(result.runs).filter((r) => r.kind !== "team" && typeof r.run_id === "string") as unknown as TeamRunRow[];
  if (!list.length) return "plan runs: none";
  const lines = list.map((r) => "  " + planRowLabel(r) + (r.error ? " — " + str(r.error).slice(0, 120) : ""));
  return "plan runs (" + list.length + "):\n" + lines.join("\n");
}

/** Jobs from the backend snapshot. */
export function jobsText(dashboard: Row): string {
  const list = rows(dashboard.jobs);
  if (!list.length) return "jobs: none recent";
  const lines = list.map((j) => {
    const attempts = j.attempts ? " · ×" + str(j.attempts) : "";
    return "  " + [str(j.id, "?"), str(j.type), str(j.status, "unknown")].filter(Boolean).join(" · ") + attempts;
  });
  return "jobs (" + list.length + ") — /job describe|cancel|retry <id>:\n" + lines.join("\n");
}

/** Endpoints from the backend snapshot. */
export function endpointsText(dashboard: Row): string {
  const list = rows(dashboard.endpoints);
  if (!list.length) return "endpoints: none running";
  const lines = list.map((e) => "  " + [str(e.id, "?"), str(e.model), str(e.status, "unknown"), str(e.backend), str(e.location)].filter((x) => x && x !== "-").join(" · "));
  return "endpoints (" + list.length + ") — /endpoint down <id>:\n" + lines.join("\n");
}

/** The snapshot's header and the probes that failed (named, never hidden). */
export function readinessText(dashboard: Row): string {
  const readiness = (dashboard.readiness ?? {}) as Row;
  const ready = readiness.ready ? "ready" : "not ready" + (readiness.error ? " — " + str(readiness.error) : "");
  const errors = Object.entries((dashboard.errors ?? {}) as Row).map(([k, v]) => "  ⚠ " + k + ": " + str(v));
  const head = "backend " + ready + " · project " + str(dashboard.project, "default") + " · gateway " + (str(dashboard.gateway) || "-");
  return [head, ...errors].join("\n");
}

export class WorkVerbs {
  constructor(private readonly deps: WorkDeps) {}

  /** `/work [show|run <wf>]`; bare is the whole pane. */
  async work(args: string): Promise<void> {
    const [verb = "", name = ""] = words(args);
    const command = ("/work " + args.trim()).trim();
    if (verb === "show" || verb === "run") {
      if (!name) return this.deps.output(command, [{ kind: "error", text: "usage: /work " + verb + " <workflow>" }]);
      return verb === "show" ? this.show(name, command) : this.run(name, command);
    }
    if (verb) return this.deps.output(command, [{ kind: "error", text: "usage: /work [show <workflow> | run <workflow>]" }]);
    await this.pane(command);
  }

  /** The pane's sections, read in parallel; a failed read is its own error line. */
  private async pane(command: string): Promise<void> {
    const dashboard = this.read("get_backend_dashboard", 30_000).then((r) => (r.dashboard ?? {}) as Row);
    const sections: Array<[string, Promise<string>]> = [
      ["workflows", this.read("workflow.list").then(catalogText)],
      ["plan runs", this.read("team.runs").then(planRunsText)],
      ["teams", this.read("teamup.list").then(teamListText)],
      ["loops", this.read("loops.list").then(loopsListText)],
      ["jobs", dashboard.then(jobsText)],
      ["endpoints", dashboard.then(endpointsText)],
    ];
    const settled = await Promise.allSettled(sections.map(([, p]) => p));
    const lines: OutputLine[] = settled.map((s, i) =>
      s.status === "fulfilled" ? { kind: "info", text: s.value } : { kind: "error", text: sections[i]![0] + ": unavailable — " + errText(s.reason) },
    );
    this.deps.output(command, lines);
  }

  private async read(method: string, timeoutMs = 20_000): Promise<Row> {
    return ((await this.deps.request(method, {}, timeoutMs)) ?? {}) as Row;
  }

  private async definition(name: string): Promise<WorkflowDefinitionDetail> {
    const wire = ((await this.deps.request("workflow.package", { workflow_name: name }, 30_000)) ?? {}) as Row;
    return { ...wire, workflow_name: str(wire.workflow_name) || name } as WorkflowDefinitionDetail;
  }

  /** `/work show <wf>`: the console's definition card, and how to run it. */
  private async show(name: string, command: string): Promise<void> {
    try {
      const detail = await this.definition(name);
      const card = workflowCardRows(detail, { width: CARD_WIDTH, expanded: null, cursor: 0 }).map((r) => r.text);
      const trigger = cardTriggerCommand(detail);
      const how = trigger.required ? "run: " + trigger.command + "  (fill the " + trigger.required + " value(s); /work run drafts it)" : "run: /work run " + name;
      this.deps.output(command, [{ kind: "info", text: [...card, "", how].join("\n") }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: "workflow.package failed: " + errText(error) }]);
    }
  }

  /** `/work run <wf>`: runs at once, or drafts the trigger line when values are needed. */
  private async run(name: string, command: string): Promise<void> {
    try {
      const trigger = cardTriggerCommand(await this.definition(name));
      if (trigger.required) {
        this.deps.draft(trigger.command);
        return this.deps.output(command, [{ kind: "info", text: name + " needs " + trigger.required + " value(s): the trigger line is in the composer — fill it and send" }]);
      }
      const result = ((await this.deps.request("workflow.run", { workflow_name: name, parameters: {}, wait: false }, 60_000)) ?? {}) as Row;
      const error = result.error ? " — " + str(result.error) : "";
      this.deps.output(command, [{ kind: result.error ? "error" : "info", text: "▶ " + name + " → " + str(result.execution_id, "?") + " · " + str(result.status, "submitted") + error }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: "workflow run failed: " + errText(error) }]);
    }
  }

  /** `/infra`: the backend snapshot — readiness, endpoints, jobs, failed probes. */
  async infra(): Promise<void> {
    try {
      const dashboard = (((await this.deps.request("get_backend_dashboard", {}, 30_000)) ?? {}) as Row).dashboard as Row | undefined;
      const d = dashboard ?? {};
      this.deps.output("/infra", [{ kind: "info", text: [readinessText(d), endpointsText(d), jobsText(d)].join("\n\n") }]);
    } catch (error) {
      this.deps.output("/infra", [{ kind: "error", text: "infra: " + errText(error) }]);
    }
  }
}
