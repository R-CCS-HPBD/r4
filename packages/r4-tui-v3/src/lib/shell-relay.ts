/**
 * `!cmd` results as people and the model see them (ADR-103 D-3, 2026-10-31):
 * the transcript preview, the framed relay the agent answers, and the line's
 * keys masked. Pure: shared by the console (`local-shell.ts`, which runs the
 * command) and the VS Code extension's bridge host.
 */
import { redactSecretFlags, scrubSecrets, secretValues } from "./secret-redact";

export type ShellRunResult = {
  command: string;
  exitCode: number;
  stdout: string;
  stderr: string;
  durationMs: number;
  /** Stopped by the user (Ctrl+C) — not relayed to the model. */
  interrupted?: boolean;
};

/**
 * Transcript preview: header line + the full output (stdout+stderr, errors
 * first). Uncapped by default (2026-10-01); pass `maxLines` to keep only the
 * trailing N lines.
 */
export function formatShellPreview(result: ShellRunResult, maxLines?: number): string {
  const buckets: string[] = [];
  const err = result.stderr.trimEnd();
  const out = result.stdout.trimEnd();
  if (err) buckets.push(err);
  if (out) buckets.push(out);
  const body = buckets.length ? buckets.join("\n").split("\n") : ["(no output)"];
  const tail = maxLines === undefined ? body : body.slice(-maxLines);
  const stopped = result.interrupted ? ", stopped with Ctrl+C — not sent to the model" : "";
  const header = `$ ${result.command}  (exit ${result.exitCode}, ${result.durationMs}ms${stopped})`;
  return [header, ...tail.map((l) => (l.length ? l : " "))].join("\n");
}

/**
 * Model-facing form of a `!cmd` result — the FULL stdout/stderr, framed so
 * the agent knows the command ran in the TUI's local shell (not its own tool
 * history) and that it should respond to the output.
 */
export function formatShellForAgent(result: ShellRunResult, where = "the TUI's local shell"): string {
  const out = result.stdout.trimEnd() || "(empty)";
  const err = result.stderr.trimEnd() || "(empty)";
  return [
    `I ran \`!${result.command}\` in ${where} — it did not run through your tools, so it is not in your tool history.`,
    `Exit code: ${result.exitCode} (${result.durationMs}ms). Respond to the output below.`,
    "",
    "--- stdout ---",
    out,
    "",
    "--- stderr ---",
    err,
  ].join("\n");
}

// ── S4: a key typed on a `!` line ─────────────────────────────────────────
// The shell runs the line as typed, but everything the console shows,
// relays to the model (and so queues on the server) or lists as a task
// carries the line with its keys masked (onboarding plan 2026-10-15 §1).

/** The `!` line as the transcript, the task list and the model may see it. */
export function shellDisplayCommand(command: string): string {
  return redactSecretFlags(command);
}

/** Free text from running `command` (output, an error), its keys scrubbed. */
export function maskShellText(command: string, text: string): string {
  return scrubSecrets(redactSecretFlags(text), secretValues(command));
}

/** The `$ …` echo; a line that held a key also says where keys belong. */
export function shellEcho(command: string): string {
  const shown = shellDisplayCommand(command);
  if (shown === command) return `$ ${command}`;
  return `$ ${shown}\n(key masked in the transcript and the model relay; the shell still saw it on ` +
    "its command line — /source add and /login hand a key to r4 on stdin)";
}

/** The result with the line's keys masked in the command and scrubbed from its output. */
export function maskShellResult(result: ShellRunResult): ShellRunResult {
  const secrets = secretValues(result.command);
  return {
    ...result,
    command: shellDisplayCommand(result.command),
    stdout: scrubSecrets(result.stdout, secrets),
    stderr: scrubSecrets(result.stderr, secrets),
  };
}
