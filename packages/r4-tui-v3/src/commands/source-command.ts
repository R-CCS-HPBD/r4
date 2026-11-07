/**
 * `/source add …` (S4, onboarding plan 2026-10-15 §1) — typed directly, or
 * asked one value at a time by the /connect wizard (2026-10-19); both end
 * here. The key goes to `r4 source add --api-key-stdin` on stdin
 * (lib/connect.ts); every notice names the source and the flags, never the
 * key. The transcript echo of the typed line is masked in the store
 * (recordUserSlashCommand) by the same word splitter and grammar
 * (lib/source-add-args.ts), so what is masked is what is parsed.
 *
 * A successful add is followed by ONE live probe of the new source
 * (2026-10-21 field report: a silent "registered" left a rejected key
 * indistinguishable from a working endpoint until the next /models). The
 * probe names its failure, counts what the source serves, or honestly says
 * it is pending (the bridge's probe cache is 5 s — a source younger than
 * that may not appear in the snapshot yet).
 */

import { actions } from "../stores";
import { addPublicSource } from "../lib/connect";
import { parseSourceAddArgs, SOURCE_ADD_USAGE, splitSlashWords } from "../lib/source-add-args";

export type SourceCommandResult = { kind: "handled" } | { kind: "error"; message: string };

interface ListedModels {
  models?: Array<{ name?: string }>;
  problems?: Array<{ source?: string; error?: string }>;
}

/** One live probe for the just-registered source, as a receipt suffix. */
export async function probeNewSource(name: string): Promise<string> {
  try {
    const { getCompanionBridge } = await import("../bridge");
    const result = (await getCompanionBridge().request("list_models", {}, 20_000)) as ListedModels;
    const mine = (result.problems ?? []).find((p) => p.source === name);
    if (mine?.error) return `probe: ${mine.error}`;
    const served = (result.models ?? []).filter((m) => m.name?.endsWith(`@${name}`)).length;
    if (served > 0) return `reachable — ${served} model${served === 1 ? "" : "s"} served`;
    return "registered; live probe pending — check /models in a few seconds";
  } catch {
    return "added; probe skipped (bridge offline) — check /models";
  }
}

/** `args` is everything after `/source` (it starts with `add`). */
export async function handleSourceAdd(args: string): Promise<SourceCommandResult> {
  const { words, unterminated } = splitSlashWords(args);
  if (unterminated) return { kind: "error", message: `unterminated quote. ${SOURCE_ADD_USAGE}` };
  const parsed = parseSourceAddArgs(words.slice(1).map((word) => word.value));
  if (!parsed.ok) return { kind: "error", message: parsed.error };
  const result = await addPublicSource(parsed.opts);
  if (!result.ok) {
    return { kind: "error", message: `source add ${parsed.opts.name} failed: ${result.output}` };
  }
  const probe = await probeNewSource(parsed.opts.name);
  actions.sendSystemNotice(
    `source ${parsed.opts.name} registered — ${result.output || "ok"} · ${probe} (/source to switch)`,
  );
  return { kind: "handled" };
}
