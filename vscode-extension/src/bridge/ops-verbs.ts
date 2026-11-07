/**
 * The ops slash verbs the console owns (ADR-103 VP-4a): `/loops`, `/traces`,
 * `/provenance`, `/sweep`. Same bridge RPCs, same texts
 * (`lib/ops-text.ts`); the answer is an output card. `/traces list|show|
 * lineage|children` goes to the bridge's `/trace`, as the console sends it.
 * No vscode import.
 */

import * as ops from "../../../packages/r4-tui-v3/src/lib/ops-text";

export type OutputLine = { kind: "info" | "error"; text: string };

export interface OpsDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  output(command: string, lines: OutputLine[]): void;
}

/** The console manifest's forwardVerbs for `traces` (the companion `/trace`). */
const TRACE_FORWARD = new Set(["list", "show", "lineage", "children"]);

function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function words(args: string): string[] {
  return args.split("\t").join(" ").split(" ").filter(Boolean);
}

export class OpsVerbs {
  constructor(private readonly deps: OpsDeps) {}

  /** Run one verb: its text, or the failure in the console's `<verb>: …` form. */
  private async card(verb: string, command: string, text: () => Promise<string>): Promise<void> {
    try {
      this.deps.output(command, [{ kind: "info", text: await text() }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: `${verb}: ${errText(error)}` }]);
    }
  }

  private usage(command: string, text: string): void {
    this.deps.output(command, [{ kind: "error", text }]);
  }

  /** `/loops [list|status|stop|pause|resume <id>|detail <id>]`. */
  async loops(args: string): Promise<void> {
    const [raw = "list", loopId = ""] = words(args);
    const sub = raw.toLowerCase();
    const command = `/loops ${args.trim()}`.trim();
    if (sub === "list") return this.card("loops", command, async () => ops.loopsListText(await this.deps.request("loops.list", {}, 15_000)));
    if (!ops.LOOPS_CONTROL_VERBS.has(sub) && sub !== "detail") return this.usage(command, ops.LOOPS_UNKNOWN);
    if (!loopId) return this.usage(command, `usage: /loops ${sub} <loop-id>`);
    if (sub === "detail") return this.card("loops", command, async () => ops.keyValueText(`loop ${loopId}`, await this.deps.request("research_loop.status", { loop_id: loopId }, 15_000)));
    return this.card("loops", command, async () => ops.loopControlText(loopId, sub, await this.deps.request(`loops.${sub}`, { loop_id: loopId }, 15_000)));
  }

  /** `/traces get <trace-id> | <job-id> | list|show|lineage|children …`. */
  async traces(args: string): Promise<void> {
    const [sub = "", arg = ""] = words(args);
    const command = `/traces ${args.trim()}`.trim();
    if (!sub) return this.usage(command, ops.TRACES_USAGE);
    if (TRACE_FORWARD.has(sub)) return this.forwardTrace(command, args.trim());
    if (sub !== "get") return this.card("traces", command, async () => ops.tracesJobText(sub, await this.deps.request("traces.job", { job_id: sub }, 15_000)));
    if (!arg) return this.usage(command, "usage: /traces get <trace-id>");
    return this.card("traces", command, async () => ops.traceGetText(arg, await this.deps.request("traces.get", { trace_id: arg }, 15_000)));
  }

  /** The companion's `/trace` answers these; its lines become the card. */
  private async forwardTrace(command: string, args: string): Promise<void> {
    await this.card("traces", command, async () => {
      const result = (await this.deps.request("dispatch_slash_command", { command: "/trace " + args }, 60_000)) as { output?: unknown };
      const lines = Array.isArray(result?.output) ? result.output.map((o) => String((o as { text?: unknown })?.text ?? o ?? "")) : [];
      return lines.filter(Boolean).join("\n") || "(no output)";
    });
  }

  /** `/provenance <job-id> [lineage]`. */
  async provenance(args: string): Promise<void> {
    const [jobId = "", raw = ""] = words(args);
    const mode = raw.toLowerCase();
    const command = `/provenance ${args.trim()}`.trim();
    if (!jobId) return this.usage(command, ops.PROVENANCE_USAGE);
    if (mode && mode !== "lineage") return this.usage(command, `provenance: unknown mode '${mode}' (use 'lineage')`);
    if (mode) return this.card("provenance", command, async () => ops.provenanceLineageText(jobId, await this.deps.request("provenance.lineage", { job_id: jobId }, 15_000)));
    return this.card("provenance", command, async () => ops.provenanceJobText(jobId, await this.deps.request("provenance.job", { job_id: jobId }, 15_000)));
  }

  /** `/sweep [list|preview <name>]`; anything else prints the help. */
  async sweep(args: string): Promise<void> {
    const [raw = "", name = ""] = words(args);
    const verb = raw.toLowerCase();
    const command = `/sweep ${args.trim()}`.trim();
    if (verb === "" || verb === "list") return this.card("sweep list failed", command, async () => ops.sweepListText(await this.deps.request("sweep.templates", {}, 15_000)));
    if (verb !== "preview") return this.deps.output(command, [{ kind: "info", text: ops.SWEEP_HELP }]);
    if (!name) return this.usage(command, ops.SWEEP_PREVIEW_USAGE);
    return this.card("sweep preview failed", command, async () => ops.sweepPreviewText(name, await this.deps.request("sweep.preview", { name }, 15_000)));
  }
}
