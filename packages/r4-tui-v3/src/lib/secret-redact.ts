/**
 * Credential masking for text the console renders, relays or keeps (S4,
 * onboarding plan 2026-10-15 §1). A key typed into a slash line (`/login
 * <token>`, `/source add … --api-key <key>`) or a `!` shell line (`r4 auth
 * login -k <token>`) must not land in the transcript, the model relay or the
 * prompt history, and a key handed to the r4 CLI must not come back in a
 * notice.
 */

import { maskSourceAddRest, SECRET_MASK } from "./source-add-args";

export { SECRET_MASK };

/** A shell-like word: quoted or bare runs with no space between (`sk"a b"`
 *  is one word); an unterminated quote runs to the end of the text; a
 *  backslash-newline (a key continued on the next line) stays in the word. */
const WORD = String.raw`(?:"[^"]*"?|'[^']*'?|\\\r?\n|\\|[^\s"'\\]+)+`;

/** `--api-key <v>` / `--api-key=<v>` / `--token <v>` / `--auth-token <v>`
 *  (`r4 mcp register`) / `--password <v>` — not `--api-key-env` or
 *  `--api-key-stdin`, whose values are a variable name or nothing, and not
 *  `--max-tokens`. */
const SECRET_FLAG_RE = new RegExp(
  String.raw`(--(?:api-key|(?:auth-|access-|hf-)?token|password|secret-key))(=|\s+)(${WORD})`,
  "gi",
);

/** `scheme://user:password@host` — the password part of a URL. */
const URL_USERINFO_RE = /(\b[a-z][a-z0-9+.-]{0,15}:\/\/[^\s/:@"']+:)([^\s/@"']+)@/gi;

/** `R4_API_KEY=<v>`, `HF_TOKEN=<v>`, `AWS_SECRET_ACCESS_KEY=<v>`, `password=<v>`:
 *  a shell assignment (or `export`) whose name says it holds a credential. */
const SECRET_ASSIGN_RE = new RegExp(
  String.raw`(^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*(?:API_?KEY|ACCESS_?KEY|SECRET_?KEY|SECRET|TOKEN|PASSWORD|PASSWD))=(${WORD})`,
  "gi",
);

/** `"api_key": "<v>"` — a credential-named key in JSON. */
const SECRET_JSON_RE =
  /("[A-Za-z0-9_-]*(?:api[_-]?key|access[_-]?key|secret[_-]?key|secret|token|password|passwd)"\s*:\s*)"([^"]*)"/gi;

/** A credential recognisable on its own: a JWT, an `sk-` key, a Hugging Face,
 *  GitHub or AWS access key. */
const BARE_SECRET_RE =
  /\b(?:eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}|sk-[A-Za-z0-9_-]{16,}|hf_[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})/g;

/** curl/wget `-u user:password` / `--user …`, masked on a curl or wget line only
 *  (`-u` means something else for `sort`, `ps`, `git`). */
const FETCH_LINE_RE = /\b(?:curl|wget)\b/;
const USER_FLAG_RE = new RegExp(String.raw`(^|\s)(-u|--user)(=|\s+)(${WORD})`, "g");

/** r4's short alias for --api-key (`r4 login -k`, `r4 auth login -k`, also
 *  `-k<v>`). Elsewhere `-k` means --knowledge, --top-k or curl's insecure,
 *  so it is masked only on a line that runs `r4 … login`. */
const R4_LOGIN_RE = /\br4\b[\s\S]*\blogin\b/i;
const SHORT_KEY_RE = new RegExp(String.raw`(^|[\s"'])(-k)(=|\s*)(${WORD})`, "g");

/** A bearer token as pasted from an HTTP header: the word, then a token-shaped run. */
const BEARER_RE = /\b(Bearer\s+)([A-Za-z0-9._~+/=-]{16,})/gi;

/** `/login <anything>` — every argument is (part of) the token. */
const LOGIN_ARGS_RE = /^(\/\s*login\s+)\S[\s\S]*$/i;
/** `/source add …` as the slash dispatcher reads it (commands/slash.ts). */
const SOURCE_ADD_RE = /^\/\s*source \s*add(?=\s|$)/i;

/** Values shorter than this are not scrubbed out of free text (a one-letter
 *  "key" would shred unrelated output); the flag masks still cover them. */
const MIN_SCRUB_LENGTH = 6;

/** Mask credential-flag values (`--api-key`, `--token`, r4 login `-k`,
 *  `Bearer`) anywhere in free text: a shell line, a CLI error. */
export function redactSecretFlags(text: string): string {
  let out = text
    .replace(SECRET_FLAG_RE, `$1$2${SECRET_MASK}`)
    .replace(BEARER_RE, `$1${SECRET_MASK}`)
    .replace(URL_USERINFO_RE, `$1${SECRET_MASK}@`)
    .replace(SECRET_ASSIGN_RE, `$1$2=${SECRET_MASK}`)
    .replace(SECRET_JSON_RE, `$1"${SECRET_MASK}"`)
    .replace(BARE_SECRET_RE, SECRET_MASK);
  if (R4_LOGIN_RE.test(out)) out = out.replace(SHORT_KEY_RE, `$1$2$3${SECRET_MASK}`);
  if (FETCH_LINE_RE.test(out)) out = out.replace(USER_FLAG_RE, `$1$2$3${SECRET_MASK}`);
  return out;
}

/** Group `group` of every match of `re` in `text`. */
function captured(text: string, re: RegExp, group: number): string[] {
  return [...text.matchAll(re)].map((m) => m[group] ?? "");
}

/** `"a b"c` → `a bc`: the value a shell word stands for. */
function unquote(word: string): string {
  return word.replace(/"([^"]*)"?|'([^']*)'?/g, (_m, dq?: string, sq?: string) => dq ?? sq ?? "");
}

/**
 * The credential values typed in `text` (as typed and unquoted), for
 * scrubbing them out of what a command printed. Only values long enough to
 * scrub safely are returned.
 */
export function secretValues(text: string): string[] {
  const words = [
    ...captured(text, SECRET_FLAG_RE, 3),
    ...captured(text, BEARER_RE, 2),
    ...captured(text, URL_USERINFO_RE, 2),
    ...captured(text, SECRET_ASSIGN_RE, 3),
    ...captured(text, SECRET_JSON_RE, 2),
    ...captured(text, BARE_SECRET_RE, 0),
  ];
  if (R4_LOGIN_RE.test(text)) words.push(...captured(text, SHORT_KEY_RE, 4));
  // `-u user:password`: curl prints neither whole, so the password also counts alone.
  if (FETCH_LINE_RE.test(text)) {
    words.push(...captured(text, USER_FLAG_RE, 4).flatMap((v) => [v, v.slice(v.indexOf(":") + 1)]));
  }
  // A key continued with backslash-newline: the command printed it joined.
  const joined = words.map((word) => word.replace(/\\\r?\n/g, ""));
  const values = new Set([...words, ...joined].flatMap((word) => [word, unquote(word)]));
  // Longest first: a value that contains another is scrubbed whole.
  return [...values].filter((value) => value.length >= MIN_SCRUB_LENGTH).sort((a, b) => b.length - a.length);
}

/** Mask credential arguments in a slash line before it is displayed or stored. */
export function redactSlashSecrets(line: string): string {
  let text = line.replace(LOGIN_ARGS_RE, `$1${SECRET_MASK}`);
  const head = SOURCE_ADD_RE.exec(text);
  if (head) text = head[0] + maskSourceAddRest(text.slice(head[0].length));
  return redactSecretFlags(text);
}

/** True when the line holds a credential (or a word that may be one): any
 *  `/login` argument, a key flag value, a stray `/source add` word. */
export function carriesSecret(line: string): boolean {
  const text = line.trim();
  return redactSlashSecrets(text) !== text;
}

/** Replace every occurrence of each known secret value in free text. */
export function scrubSecrets(text: string, secrets: ReadonlyArray<string | undefined>): string {
  let out = text;
  for (const secret of secrets) {
    if (secret) out = out.split(secret).join(SECRET_MASK);
  }
  return out;
}
