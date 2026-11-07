/**
 * /connect helpers — r4 auth + source verbs via the CLI (no bridge round-trip).
 *
 * S4 (onboarding plan 2026-10-15 §1): a credential never rides argv — any
 * local user can read a process's command line. A provider key goes to
 * `r4 source add … --api-key-stdin` and a JWT to `r4 auth login
 * --api-key-stdin`, both on stdin; every text the console shows from the CLI
 * (result, error, spawn failure, timeout) is scrubbed of the secrets it
 * handled. An r4 without the stdin flag fails closed with an upgrade hint —
 * the console never falls back to argv.
 */

import { resolveR4Command } from "./resolve-python";
import { parseTerminalSafe, terminalSafe } from "./terminal-safe";
import { redactSecretFlags, scrubSecrets } from "./secret-redact";
import { splitSlashWords, type PublicSourceOptions } from "./source-add-args";

// The `/source add` grammar lives with its echo mask (one walker for both).
export { parseSourceAddArgs, SOURCE_ADD_USAGE, type PublicSourceOptions } from "./source-add-args";

interface RunOptions {
  timeoutMs?: number;
  /** Written to the child's stdin, then EOF (a credential). Absent → stdin is /dev/null. */
  stdin?: string;
}

interface RunOutcome {
  stdout: string;
  stderr: string;
  exitCode: number | null;
  /** Console-authored failure (r4 missing, timeout, killed) — wins over CLI text. */
  failure?: string;
}

const DEFAULT_TIMEOUT_MS = 30_000;

export const R4_MISSING = "r4 CLI not found — install the r4 client or set R4_TUI_PYTHON";

function spawnR4(args: string[], stdin: string | undefined, signal: AbortSignal) {
  return Bun.spawn([...resolveR4Command(), ...args], {
    stdin: stdin === undefined ? "ignore" : new TextEncoder().encode(stdin),
    stdout: "pipe",
    stderr: "pipe",
    signal,
  });
}

async function runR4(
  args: string[],
  { timeoutMs = DEFAULT_TIMEOUT_MS, stdin }: RunOptions = {},
): Promise<RunOutcome> {
  const signal = AbortSignal.timeout(timeoutMs);
  let proc: ReturnType<typeof spawnR4>;
  try {
    proc = spawnR4(args, stdin, signal);
  } catch {
    // ENOENT & co.: say what is wrong in the console's own words.
    return { stdout: "", stderr: "", exitCode: null, failure: R4_MISSING };
  }
  // Drain both pipes while waiting: a full stderr pipe must not stall stdout.
  const stdoutText = new Response(proc.stdout).text().catch(() => "");
  const stderrText = new Response(proc.stderr).text().catch(() => "");
  await proc.exited;
  if (proc.exitCode === null) {
    // Killed (no exit code) — never a success. A grandchild may still hold
    // the pipes open, so do not wait for them to drain.
    const failure = signal.aborted
      ? `r4 timed out after ${timeoutMs} ms`
      : `r4 was killed (${proc.signalCode ?? "signal"})`;
    return { stdout: "", stderr: "", exitCode: null, failure };
  }
  const [stdout, stderr] = await Promise.all([stdoutText, stderrText]);
  // CLI output reaches the screen (c3-tui D-3): no live control characters.
  return { stdout: terminalSafe(stdout), stderr: terminalSafe(stderr), exitCode: proc.exitCode };
}

export interface CliResult {
  ok: boolean;
  output: string;
}

function succeeded(out: RunOutcome): boolean {
  return out.failure === undefined && out.exitCode === 0;
}

/** What the console may show: its own failure text, else the CLI's. */
function shownText(out: RunOutcome): string {
  return out.failure ?? (out.stderr || out.stdout);
}

function toResult(out: RunOutcome, secrets: ReadonlyArray<string | undefined> = []): CliResult {
  // Scrub before slicing: a cut must never leave a partial key behind.
  const text = redactSecretFlags(scrubSecrets(shownText(out).trim(), secrets));
  return { ok: succeeded(out), output: text.slice(0, 300) };
}

/** Arbitrary r4 CLI invocation (overlay helpers). */
export async function runCli(args: string[], { timeoutMs }: { timeoutMs?: number } = {}): Promise<CliResult> {
  return toResult(await runR4(args, { timeoutMs }));
}

export const STDIN_FLAG_MISSING =
  "this r4 cannot read a key from stdin (no --api-key-stdin) — upgrade the r4 client; " +
  "the console never puts a key on the command line";

/** An r4 too old for `--api-key-stdin` fails in argparse. The error line
 *  follows a long usage block, so test the full text, not the 300-char cut. */
function lacksStdinFlag(out: RunOutcome): boolean {
  return !succeeded(out) && /unrecognized arguments:.*--api-key-stdin/.test(shownText(out));
}

/** Run r4 with a credential on stdin; the result never carries the credential. */
async function runWithSecret(args: string[], secret: string, timeoutMs?: number): Promise<CliResult> {
  const out = await runR4(args, { timeoutMs, stdin: secret });
  if (lacksStdinFlag(out)) return { ok: false, output: STDIN_FLAG_MISSING };
  return toResult(out, [secret]);
}

/** Authenticate with a JWT API key / onboarding token (`r4 auth login --api-key-stdin`). */
export async function loginWithJwt(jwt: string, { timeoutMs }: { timeoutMs?: number } = {}): Promise<CliResult> {
  return runWithSecret(["auth", "login", "--api-key-stdin"], jwt, timeoutMs);
}

export const LOGIN_USAGE = "usage: /login <JWT-token>  (see /connect for the menu) — nothing was saved";

/** Words a user types to ask for usage — never a token (slash-b#0: `/login
 *  help` stored "help" as the key and said "login ok"). */
const LOGIN_HELP_WORDS = new Set(["help", "-h", "--help", "?", "usage"]);

/** The token of a `/login` line, or null when the line holds none: empty,
 *  a help word, a flag, several words, an open quote. A pasted
 *  "bearer" word before the token is dropped. Quotes group like every slash line. */
export function loginTokenOf(args: string): string | null {
  const { words, unterminated } = splitSlashWords(args);
  const values = words.map((word) => word.value);
  if (values.length === 2 && /^bearer$/i.test(values[0]!)) values.shift();
  const token = values[0];
  if (unterminated || values.length !== 1 || !token) return null;
  if (LOGIN_HELP_WORDS.has(token.toLowerCase()) || token.startsWith("-")) return null;
  return token;
}

/** The post-login receipt from `r4 auth status` (exit 0 = the gateway
 *  accepts the credential). `r4 auth login` stores without asking the
 *  gateway, so a rejected token is named here as rejected — never "login
 *  ok", never a bare "auth status unclear; try /health" (connect#6). */
export function loginReceipt(status: AuthStatus): { ok: boolean; text: string } {
  if (status.ok) {
    const who = status.identity ? `signed in as ${status.identity}` : "signed in";
    const exp = status.expires ? ` · token until ${status.expires}` : "";
    return { ok: true, text: `bridge reconnected — ${who}${exp}` };
  }
  if (/rejected/i.test(status.raw)) {
    return {
      ok: false,
      text: "token rejected by the gateway — it is stored but does not sign you in; paste a valid token via /connect (or /login <token>)",
    };
  }
  return { ok: true, text: `bridge reconnected — not signed in: ${firstOutputLine(status.raw) || "r4 auth status failed"}` };
}

/** argv for `r4 source add … --kind public` — never contains the key itself. */
export function publicSourceArgs(opts: PublicSourceOptions): string[] {
  const args = ["source", "add", opts.name, "--url", opts.url, "--kind", "public"];
  if (opts.apiKey) args.push("--api-key-stdin");
  else if (opts.apiKeyEnv) args.push("--api-key-env", opts.apiKeyEnv);
  if (opts.provider) args.push("--provider", opts.provider);
  if (opts.description) args.push("--description", opts.description);
  if (opts.allowInsecureHttp) args.push("--allow-insecure-http");
  return args;
}

/** Add a public OpenAI-compatible source; the key goes to r4 on stdin. */
export async function addPublicSource(
  opts: PublicSourceOptions,
  { timeoutMs }: { timeoutMs?: number } = {},
): Promise<CliResult> {
  const args = publicSourceArgs(opts);
  if (opts.apiKey) return runWithSecret(args, opts.apiKey, timeoutMs);
  return toResult(await runR4(args, { timeoutMs }));
}

/** The first non-empty line of a CLI result (r4 prints its outcome first). */
export function firstOutputLine(output: string): string {
  return output.split("\n").map((line) => line.trim()).find(Boolean) ?? "";
}

/** The /connect logout notice: r4's own outcome ("✓ Logged out …" or
 *  "ℹ Already logged out …"), never a fixed "logged out" (connect#1). */
export function logoutNoticeText(res: CliResult): string {
  if (!res.ok) return `logout failed: ${res.output}`;
  const said = firstOutputLine(res.output) || "logged out";
  return /already logged out/i.test(said) ? said : `${said} · reconnecting companion bridge…`;
}

/** Add the local Ollama source (one-shot convenience verb). */
export async function addOllama(): Promise<CliResult> {
  return toResult(await runR4(["source", "add-ollama"]));
}

export interface AuthStatus {
  ok: boolean;
  identity?: string;
  expires?: string;
  raw: string;
}

/** `r4 auth status` parser (tolerant — text or JSON). The CLI has no
 * `--json` there: argparse exited 2 on it and every user read "not signed
 * in" (TUI audit 2026-10-17). Exit 0 = credentials the gateway accepts.
 * Its text output is the JWT identity block (_show_jwt_identity): the
 * `Username:` / `Expires in:` lines are lifted so the sign-in receipt can
 * name who and until when (2026-10-21, receipt said a bare "signed in"). */
export async function authStatus(): Promise<AuthStatus> {
  const out = await runR4(["auth", "status"], { timeoutMs: 20_000 });
  const ok = succeeded(out);
  const raw = shownText(out);
  try {
    const obj = parseTerminalSafe(out.stdout);
    if (obj && typeof obj === "object") {
      const rec = obj as Record<string, unknown>;
      return {
        ok,
        identity: rec.user_id == null ? undefined : String(rec.user_id),
        expires: rec.expires_at == null ? undefined : String(rec.expires_at),
        raw,
      };
    }
  } catch {
    /* fall through */
  }
  const username = out.stdout.match(/^\s*Username:\s*(.+?)\s*$/m)?.[1];
  const expiresIn = out.stdout.match(/Expires in:\s*(.+?)\s*$/m)?.[1];
  return {
    ok,
    identity: username ?? undefined,
    expires: expiresIn ?? undefined,
    raw: raw.trim(),
  };
}
