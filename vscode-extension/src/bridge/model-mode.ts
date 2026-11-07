/**
 * `/models` and `/mode` as the console answers them (ADR-103 VP-2e).
 *
 * Bare `/models` (or `list`) is a picker over the bridge's `list_models`: the
 * current model marked, and the named sources whose probe failed said aloud
 * (an empty picker must say "rikyu: API key rejected", not "no models").
 * `/models default <name>` and other forms go to the bridge. `/mode <chat|plan|
 * autopilot>` switches the interaction mode; `/mode write-plan on|off` is its
 * own verb (`set_write_requires_plan`); bare `/mode` goes to the bridge.
 * No vscode import.
 */

export type OutputLine = { kind: "info" | "error"; text: string };

export interface ModelRow {
  name: string;
  label: string;
}

export interface ModelModeDeps {
  request(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<unknown>;
  output(command: string, lines: OutputLine[]): void;
  setModel(model: string): Promise<void>;
  /** The bridge's mode name (chat | plan | autopilot). */
  setMode(mode: string): Promise<void>;
  pickModel?(ask: { models: ModelRow[]; current: string; problems: string[] }): Promise<string | null>;
}

export const MODE_NAMES = ["chat", "plan", "autopilot"] as const;

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function errText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** True for the `/models` forms the picker answers. */
export function claimsModels(args: string): boolean {
  const verb = args.trim().toLowerCase();
  return verb === "" || verb === "list" || verb === "ls";
}

/** True for the `/mode` forms answered here (a mode name, write-plan on|off). */
export function claimsMode(args: string): boolean {
  const target = args.trim().toLowerCase();
  return (MODE_NAMES as readonly string[]).includes(target) || target === "write-plan on" || target === "write-plan off";
}

/** A `list_models` answer → rows, the current model, and the named problems. */
export function modelChoices(result: unknown): { models: ModelRow[]; current: string; problems: string[] } {
  const r = result && typeof result === "object" ? (result as Record<string, unknown>) : {};
  const rows = Array.isArray(r.models) ? (r.models as Record<string, unknown>[]) : [];
  const models = rows.map((m) => ({ name: str(m.name), label: str(m.label) })).filter((m) => m.name);
  const problems = (Array.isArray(r.problems) ? (r.problems as Record<string, unknown>[]) : [])
    .map((p) => [str(p.source), str(p.error)].filter(Boolean).join(": "))
    .filter(Boolean);
  return { models, current: str(r.current), problems };
}

export class ModelMode {
  constructor(private readonly deps: ModelModeDeps) {}

  /** `/models`: pick the session model. */
  async models(): Promise<void> {
    let choices: ReturnType<typeof modelChoices>;
    try {
      choices = modelChoices(await this.deps.request("list_models", {}, 30_000));
    } catch (error) {
      return this.deps.output("/models", [{ kind: "error", text: `models failed: ${errText(error)}` }]);
    }
    if (!choices.models.length) {
      const why = choices.problems.length ? choices.problems.join("; ") : "no provisioned models";
      return this.deps.output("/models", [{ kind: "error", text: `no models to pick — ${why}` }]);
    }
    const picked = await this.deps.pickModel?.(choices);
    if (!picked || picked === choices.current) return;
    await this.deps.setModel(picked);
    this.deps.output("/models", [{ kind: "info", text: `model → ${picked}` }]);
  }

  /** `/mode <name>` and `/mode write-plan on|off`. */
  async mode(args: string): Promise<void> {
    const target = args.trim().toLowerCase();
    const command = `/mode ${target}`;
    if (target.startsWith("write-plan ")) return this.writePlan(target.endsWith(" on"), command);
    try {
      await this.deps.setMode(target);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: `mode not changed: ${errText(error)}` }]);
    }
  }

  private async writePlan(enabled: boolean, command: string): Promise<void> {
    try {
      const result = (await this.deps.request("set_write_requires_plan", { enabled }, 15_000)) as { write_requires_plan?: boolean };
      this.deps.output(command, [{ kind: "info", text: `write_requires_plan → ${result?.write_requires_plan ? "on" : "off"}` }]);
    } catch (error) {
      this.deps.output(command, [{ kind: "error", text: `write-plan failed: ${errText(error)}` }]);
    }
  }
}
