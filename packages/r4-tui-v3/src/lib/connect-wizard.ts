/**
 * /connect wizard — question-and-answer form behind the /connect menu.
 *
 * The "Login with JWT" and "Add public endpoint" rows used to seed a
 * half-typed slash line in the composer and left every value (`/login
 * <token>`, `/source add <name> --url <url> --api-key <k>`) to the user.
 * The wizard asks for each value one prompt at a time, then BUILDS the same
 * line and dispatches it through dispatchSlashFromChat — one grammar
 * (lib/source-add-args.ts) still parses the line, masks its transcript
 * echo, and puts every secret on r4's stdin, never argv (S4).
 *
 * 2026-10-20 field report: a 512-char silent draft cap mangled every
 * onboarding JWT longer than it (HS256 ≈470–650 chars, RS256 ≈770–900):
 * login "succeeded" with a truncated-token credential and only the CLI
 * worked. Caps are now per-question (`maxDraft`) and the overlay refuses
 * overflow LOUDLY. A credentialed http:// URL needs r4's G-4 opt-in, so a
 * sixth question asks it the moment the answers demand `--allow-insecure-http`.
 *
 * Pure — no imports, no I/O; the overlay drives the flow, tests drive this.
 */

export type ConnectFlow = "login" | "source";

export interface ConnectQuestion {
  key: string;
  prompt: string;
  /** Extra honesty line shown with the prompt (where the value goes). */
  hint?: string;
  secret: boolean;
  required: boolean;
  /** The overlay refuses more input past this, with a loud error. */
  maxDraft: number;
  /** Answer rejected with this text; absent or null accepts. */
  validate?: (value: string) => string | null;
}

/** Prose answers (names, URLs, provider, description). */
export const PROSE_DRAFT_MAX = 512;
/** Secret answers: onboarding JWTs run well past 500 chars (HS256 ≈470–650,
 *  RS256 ≈770–900) and provider keys are not small either. */
export const SECRET_DRAFT_MAX = 4096;

export const CONNECT_WIZARD_TITLE: Record<ConnectFlow, string> = {
  login: "connect — login with JWT / onboarding token",
  source: "connect — add a public OpenAI-compatible endpoint",
};

function noSpaces(value: string): string | null {
  return /\s/.test(value) ? "no spaces — paste the value alone" : null;
}

function httpUrl(value: string): string | null {
  return /^https?:\/\/\S+$/.test(value) ? null : "the url starts with http:// or https://";
}

function optionalNoSpaces(value: string): string | null {
  return value ? noSpaces(value) : null;
}

/** The "yes" that opts a credential into plain http (r4's G-4). */
export const HTTP_CREDENTIAL_OPT_IN = /^y(?:es)?$/i;

function yesOrNo(value: string): string | null {
  if (HTTP_CREDENTIAL_OPT_IN.test(value) || /^n(?:o)?$/i.test(value)) return null;
  return "yes or no — Enter refuses";
}

/** Non-localhost hosts of the four literals r4's G-4 treats as on-machine. */
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"]);

/** Whether the source answers so far need r4's G-4 opt-in question: a
 *  credential (answers[2]) for a non-localhost http:// URL (answers[1]). */
function needsInsecureHttpQuestion(answers: readonly string[]): boolean {
  const url = (answers[1] ?? "").trim().toLowerCase();
  if (!url.startsWith("http://") || !(answers[2] ?? "").trim()) return false;
  const bracketed = url.match(/^http:\/\/(\[[^\]]*\])/);
  const host = bracketed ? bracketed[1]! : url.slice(7).split(/[/:]/, 1)[0]!;
  return !LOCAL_HOSTS.has(host);
}

/** The source-name check: one word, not a flag, not a name already
 *  registered — re-adding one replaced its row and wiped its stored key
 *  (connect#4, 2026-10-21); `taken` is the names `r4 source list` gave. */
function sourceName(taken: readonly string[]): (value: string) => string | null {
  return (value) => {
    if (value.startsWith("-")) return "a name, not a flag — start with a letter or digit";
    if (taken.includes(value)) {
      return `source '${value}' already exists — choose another name (/source lists them; /source remove it first to replace it)`;
    }
    return noSpaces(value);
  };
}

const SOURCE_NAME_QUESTION: Omit<ConnectQuestion, "validate"> = {
  key: "name",
  prompt: "Source name (e.g. rikyu)",
  secret: false,
  required: true,
  maxDraft: PROSE_DRAFT_MAX,
};

const SOURCE_DETAIL_QUESTIONS: ConnectQuestion[] = [
  {
    key: "url",
    prompt: "Base URL (OpenAI-compatible, e.g. https://host/v1)",
    secret: false,
    required: true,
    maxDraft: PROSE_DRAFT_MAX,
    validate: httpUrl,
  },
  {
    key: "apiKey",
    prompt: "API key — optional, Enter skips",
    hint: "masked; goes to r4 on stdin, never on the command line",
    secret: true,
    required: false,
    maxDraft: SECRET_DRAFT_MAX,
  },
  {
    key: "provider",
    prompt: "Provider — optional (e.g. openai), Enter skips",
    secret: false,
    required: false,
    maxDraft: PROSE_DRAFT_MAX,
    validate: optionalNoSpaces,
  },
  { key: "description", prompt: "Description — optional, Enter skips", secret: false, required: false, maxDraft: PROSE_DRAFT_MAX },
];

/** The prompts of a flow, in ask order; `answers` so far decide the
 *  conditional tail (the G-4 opt-in appears only when the URL+key demand
 *  it); `taken` names the registered sources the name may not reuse. The
 *  final Enter runs the line. */
export function connectQuestions(
  flow: ConnectFlow,
  answers: readonly string[] = [],
  taken: readonly string[] = [],
): ConnectQuestion[] {
  if (flow === "login") {
    return [
      {
        key: "token",
        prompt: "Paste your JWT / onboarding token",
        hint: "masked; goes to r4 on stdin, never on the command line",
        secret: true,
        required: true,
        maxDraft: SECRET_DRAFT_MAX,
        validate: noSpaces,
      },
    ];
  }
  const source = [{ ...SOURCE_NAME_QUESTION, validate: sourceName(taken) }, ...SOURCE_DETAIL_QUESTIONS];
  if (needsInsecureHttpQuestion(answers)) {
    return [
      ...source,
      {
        key: "allowInsecureHttp",
        prompt: "The key would travel plain http — type yes to allow, Enter to refuse",
        hint: "r4 refuses a credential for a non-localhost http:// URL without this (G-4)",
        secret: false,
        required: false,
        maxDraft: 16,
        validate: yesOrNo,
      },
    ];
  }
  return source;
}

/** Shown when the G-4 question is refused: nothing runs, the form goes back
 *  to the URL question (connect#8 — a refusal still dispatched the add, r4
 *  refused it, and the user was told to pass the flag they had declined). */
export const INSECURE_HTTP_REFUSED =
  "not added — the key would travel plain http: enter an https URL, or leave the key empty";

/** Whether the final answers refuse the G-4 opt-in the URL+key demand. */
export function refusesInsecureHttp(flow: ConnectFlow, answers: readonly string[]): boolean {
  if (flow !== "source" || !needsInsecureHttpQuestion(answers)) return false;
  return !HTTP_CREDENTIAL_OPT_IN.test((answers[5] ?? "").trim());
}

/** One word of the built line that the slash splitter (lib/source-add-args
 *  splitSlashWords) reads back as exactly `value`, whatever it holds.
 *  Single quotes keep every character literal (backslashes and `"` too); a
 *  `'` itself closes the quote, takes `"'"` and reopens it — adjacent quoted
 *  runs join into one word. Before 2026-10-21 only spaces and `"` were
 *  quoted, so "Bob's server" opened a quote that never closed (connect#5). */
export function quoteSlashWord(value: string): string {
  if (value && !/[\s'"]/.test(value)) return value;
  return `'${value.replace(/'/g, `'"'"'`)}'`;
}

const word = quoteSlashWord;

/** `--flag value`; a value that itself starts with `--` rides inline
 *  (`--flag=--value`) so the grammar never reads it as the next flag. */
function flagWord(flag: string, value: string): string {
  return value.startsWith("--") ? word(`${flag}=${value}`) : `${flag} ${word(value)}`;
}

/** The slash line the final Enter dispatches; answers are question-ordered,
 *  a skipped optional is "". */
export function buildWizardLine(flow: ConnectFlow, answers: readonly string[]): string {
  if (flow === "login") return `/login ${word(answers[0] ?? "")}`;
  const [name = "", url = "", apiKey = "", provider = "", description = "", allowHttp = ""] = answers;
  const parts = ["/source add", word(name), flagWord("--url", url)];
  if (apiKey) parts.push(flagWord("--api-key", apiKey));
  if (provider) parts.push(flagWord("--provider", provider));
  if (description) parts.push(flagWord("--description", description));
  if (HTTP_CREDENTIAL_OPT_IN.test(allowHttp)) parts.push("--allow-insecure-http");
  return parts.join(" ");
}
