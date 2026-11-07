/**
 * `/source add` argument grammar (S4, onboarding plan 2026-10-15 §1). One
 * module serves both readers of the line, so they cannot drift apart: the
 * parser that builds the r4 call (lib/connect.ts) and the mask that decides
 * which words the transcript echo and the prompt history may keep
 * (lib/secret-redact.ts). Pure — no imports, no I/O.
 */

export interface PublicSourceOptions {
  name: string;
  url: string;
  /** The key itself — sent on stdin, never on the command line. */
  apiKey?: string;
  /** Name of an environment variable holding the key (only the name rides argv). */
  apiKeyEnv?: string;
  provider?: string;
  description?: string;
  allowInsecureHttp?: boolean;
}

export const SECRET_MASK = "•••";

type ValueField = "url" | "apiKey" | "apiKeyEnv" | "provider" | "description";

const VALUE_FLAGS: Readonly<Record<string, ValueField>> = {
  "--url": "url",
  "--api-key": "apiKey",
  "--api-key-env": "apiKeyEnv",
  "--provider": "provider",
  "--description": "description",
};

const BOOL_FLAG = "--allow-insecure-http";

export const SOURCE_ADD_USAGE =
  "usage: /source add <name> --url <url> [--api-key <key> | --api-key-env <VAR>] " +
  "[--provider <p>] [--description <text>] [--allow-insecure-http]";

// ── Words ────────────────────────────────────────────────────────────────

/** One word of a slash line: its value (quotes removed) and its raw span. */
export interface SlashWord {
  value: string;
  start: number;
  end: number;
}

interface WordScan {
  words: SlashWord[];
  value: string;
  start: number;
  quote: '"' | "'" | null;
}

function endWord(scan: WordScan, end: number): void {
  if (scan.value) scan.words.push({ value: scan.value, start: scan.start, end });
  scan.value = "";
  scan.start = -1;
}

/** One character inside quotes; returns how many extra characters it used. */
function quotedChar(scan: WordScan, text: string, i: number): number {
  const char = text[i]!;
  if (char === scan.quote) {
    scan.quote = null;
    return 0;
  }
  if (char === "\\" && scan.quote === '"' && text[i + 1] === '"') {
    scan.value += '"';
    return 1;
  }
  scan.value += char;
  return 0;
}

function bareChar(scan: WordScan, char: string, i: number): void {
  if (/\s/.test(char)) {
    endWord(scan, i);
    return;
  }
  if (scan.start < 0) scan.start = i;
  if (char === "'" || char === '"') scan.quote = char;
  else scan.value += char;
}

/**
 * Split like `tokenizeSlashArgs` (commands/slash.ts): quotes group, `\"`
 * inside double quotes is a quote, empty words are dropped. Each word keeps
 * its raw span so a mask can replace exactly what was typed. `unterminated`
 * marks an open quote (where the slash tokenizer throws).
 */
export function splitSlashWords(text: string): { words: SlashWord[]; unterminated: boolean } {
  const scan: WordScan = { words: [], value: "", start: -1, quote: null };
  for (let i = 0; i < text.length; i += 1) {
    if (scan.quote) i += quotedChar(scan, text, i);
    else bareChar(scan, text[i]!, i);
  }
  const unterminated = scan.quote !== null;
  endWord(scan, text.length);
  return { words: scan.words, unterminated };
}

// ── Parser ───────────────────────────────────────────────────────────────

/** Split `--flag=value` into its parts (a flag without `=` → no inline value). */
function splitInline(token: string): [string, string | undefined] {
  const eq = token.indexOf("=");
  return eq > 0 ? [token.slice(0, eq), token.slice(eq + 1)] : [token, undefined];
}

/** A flag's value: inline (`--f=v`) or the next token — never another flag. */
function flagValue(tokens: readonly string[], i: number, inline: string | undefined): string | undefined {
  if (inline !== undefined) return inline || undefined;
  const next = tokens[i + 1];
  return next && !next.startsWith("--") ? next : undefined;
}

/** A flag word an error may repeat: short lowercase words joined by `-`. */
const PLAIN_FLAG = /^--[a-z]+(?:-[a-z]+)*$/;
const PLAIN_FLAG_MAX = 24;

/**
 * The error for an unknown flag. It names the flag only when the word has a
 * plain flag shape: a key glued onto a misspelt flag (`--apikey<k>`,
 * `--api-key:<k>`) makes the whole word the "flag", so any other shape is
 * withheld (S4: a key never reaches a notice).
 */
function unknownFlagError(flag: string): string {
  const plain = flag.length <= PLAIN_FLAG_MAX && PLAIN_FLAG.test(flag);
  const shown = plain ? flag : "(not shown — it may hold a key)";
  return `unknown flag ${shown}. ${SOURCE_ADD_USAGE}`;
}

/**
 * Consume tokens[i] (plus its value when separate). Returns how many extra
 * tokens were consumed, or an error that names a known flag or a plain-shaped
 * unknown one — never a value, never a word that may hold one.
 */
function consumeToken(
  tokens: readonly string[],
  i: number,
  opts: Partial<PublicSourceOptions>,
  names: string[],
): number | string {
  const [flag, inline] = splitInline(tokens[i]!);
  if (flag === BOOL_FLAG) {
    opts.allowInsecureHttp = true;
    return 0;
  }
  if (!flag.startsWith("--")) {
    names.push(tokens[i]!);
    return 0;
  }
  const field = VALUE_FLAGS[flag];
  if (!field) return unknownFlagError(flag);
  const value = flagValue(tokens, i, inline);
  if (!value) return `${flag} needs a value. ${SOURCE_ADD_USAGE}`;
  opts[field] = value;
  return inline === undefined ? 1 : 0;
}

/** Parse `/source add` tokens (after `add`) into options (pure). */
export function parseSourceAddArgs(
  tokens: readonly string[],
): { ok: true; opts: PublicSourceOptions } | { ok: false; error: string } {
  const opts: Partial<PublicSourceOptions> = {};
  const names: string[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const step = consumeToken(tokens, i, opts, names);
    if (typeof step === "string") return { ok: false, error: step };
    i += step;
  }
  if (names.length !== 1 || !opts.url) return { ok: false, error: SOURCE_ADD_USAGE };
  return { ok: true, opts: { ...opts, name: names[0]!, url: opts.url } };
}

// ── Mask ─────────────────────────────────────────────────────────────────

/** Masks for an unknown flag: the flag and the value it would take (a
 *  misspelt `--apikey <k>` / `--token <k>` carries the key all the same). */
function unknownFlagMasks(tokens: readonly string[], i: number, inline: string | undefined): string[] {
  const takesNext = inline === undefined && flagValue(tokens, i, undefined) !== undefined;
  return takesNext ? [SECRET_MASK, SECRET_MASK] : [SECRET_MASK];
}

/** Masks for the flag at tokens[i] and the value it consumes (null = shown as typed). */
function flagMasks(tokens: readonly string[], i: number): Array<string | null> {
  const [flag, inline] = splitInline(tokens[i]!);
  if (flag === BOOL_FLAG) return [inline === undefined ? null : `${flag}=${SECRET_MASK}`];
  const field = VALUE_FLAGS[flag];
  if (!field) return unknownFlagMasks(tokens, i, inline);
  const secret = field === "apiKey";
  if (inline !== undefined) return [secret ? `${flag}=${SECRET_MASK}` : null];
  if (flagValue(tokens, i, undefined) === undefined) return [null];
  return [null, secret ? SECRET_MASK : null];
}

/**
 * Which `/source add` words (after `add`) may be shown, per word: null keeps
 * it as typed, a string replaces it. Shown: the name (only when exactly one
 * positional — with several, any may be a stray key), the known flags, and
 * the values of --url / --provider / --description / --api-key-env.
 * Masked: the --api-key value, unknown flags with their values, every extra
 * positional (pure; walks the tokens the way parseSourceAddArgs does).
 */
export function sourceAddMasks(tokens: readonly string[]): Array<string | null> {
  const masks: Array<string | null> = [];
  const positionals: number[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    if (!tokens[i]!.startsWith("--")) {
      positionals.push(masks.length);
      masks.push(null);
      continue;
    }
    const step = flagMasks(tokens, i);
    masks.push(...step);
    i += step.length - 1;
  }
  if (positionals.length > 1) for (const at of positionals) masks[at] = SECRET_MASK;
  return masks;
}

/** `rest` (the text after `/source add`) with every masked word replaced. */
export function maskSourceAddRest(rest: string): string {
  const { words, unterminated } = splitSlashWords(rest);
  if (unterminated) return ` ${SECRET_MASK}`; // an open quote: keep nothing
  const masks = sourceAddMasks(words.map((word) => word.value));
  let out = "";
  let at = 0;
  words.forEach((word, k) => {
    const mask = masks[k];
    if (mask === null || mask === undefined) return;
    out += rest.slice(at, word.start) + mask;
    at = word.end;
  });
  return out + rest.slice(at);
}
