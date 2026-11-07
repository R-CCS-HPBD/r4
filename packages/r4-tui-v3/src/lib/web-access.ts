/**
 * The agent's web access for a session — the user's choice (ADR-099 D-2).
 *
 * The console asks when a session opens (local or remote), on the first
 * submit of a session that has no choice yet, and on `/web`. A remote
 * question offers only what the server's caps allow (`session.venue_policy`:
 * `web.allow`, a domain cap removes `public`). Pure helpers, bridge-free;
 * the Python side is `r4/src/r4/companion/web_access.py`.
 */

export type WebMode = "none" | "allowlist" | "public";
export const WEB_MODES: readonly WebMode[] = ["none", "allowlist", "public"];
export const MAX_DOMAINS = 64;

/** The session's choice (`snapshot.session.web_access`); mode "" = not chosen yet. */
export interface WebAccessChoice {
  mode: WebMode | "";
  domains: string[];
  chosenAt: string;
  source: string;
}

/** What the console sends: the mode and, for an allowlist, its domains. */
export interface WebChoice {
  mode: WebMode;
  domains: string[];
}

/** The caps the question honours (from the preview's effective limits). */
export interface WebCaps {
  allow: WebMode[];
  domains: string[];
  suggested: string[];
}

export const NO_CAPS: WebCaps = { allow: [...WEB_MODES], domains: [], suggested: [] };

export const WEB_MODE_TEXT: Record<WebMode, { label: string; consequence: string }> = {
  none: { label: "Off", consequence: "the agent's web tools refuse every call" },
  allowlist: { label: "Allowlist", consequence: "only the domains you list (.a.org = with subdomains)" },
  public: { label: "Public", consequence: "any web host" },
};

/** ADR-101 D-6: the local note depends on the session's sandbox, like the
 * CLI's `web_access_prompt.local_note`, in the console's own words (it names
 * /sandbox, which the CLI has not, and fits the dialog's two note rows).
 * Off: nothing below the agent's own web tools is filtered; on: the shell
 * and Python reach the web only through the sandbox proxy, which follows
 * this choice (local MCP servers keep their own sandbox web entry, D-9). */
export const LOCAL_NOTE =
  "Shell, Python and MCP tools are not filtered locally (turn on /sandbox to filter them): they use your network after you approve them.";
export const LOCAL_NOTE_SANDBOXED =
  "Sandbox on: shell and Python go through its proxy with this choice; local MCP servers use their own web setting.";

/** The local note for a session's sandbox (`activeTab().sandbox`). */
export function localNote(sandbox: { mode: string } | null | undefined): string {
  return sandbox?.mode === "on" ? LOCAL_NOTE_SANDBOXED : LOCAL_NOTE;
}

/** A note over two rows of `width` cells, split at a word: the dialog's
 * note row no longer cuts the `/sandbox` hint off its tail. */
export function noteRows(text: string, width: number): [string, string] {
  if (text.length <= width) return [text, ""];
  const cut = text.lastIndexOf(" ", width);
  const at = cut > 0 ? cut : width;
  return [text.slice(0, at), text.slice(at).trim()];
}
export const REMOTE_NOTE = "The sandbox proxy filters all of this session's traffic.";

const DOMAIN_RE = /^\.?[a-z0-9-]+(\.[a-z0-9-]+)+$/;

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => String(v)).filter((v) => v.trim() !== "") : [];
}

function isMode(value: unknown): value is WebMode {
  return typeof value === "string" && (WEB_MODES as readonly string[]).includes(value);
}

/** Bridge `web_access` → choice; null when the payload does not carry one. */
export function mapWebAccess(raw: unknown): WebAccessChoice | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  return {
    mode: isMode(r.mode) ? r.mode : "",
    domains: strings(r.domains),
    chosenAt: typeof r.chosen_at === "string" ? r.chosen_at : "",
    source: typeof r.source === "string" ? r.source : "",
  };
}

/** "off" · "allowlist (a, b)" · "public" · "not chosen". */
export function webAccessLabel(choice: { mode: string; domains: string[] } | null | undefined): string {
  if (!choice || !isMode(choice.mode)) return "not chosen";
  if (choice.mode === "none") return "off";
  if (choice.mode === "allowlist") return `allowlist (${choice.domains.join(", ")})`;
  return "public";
}

/** The chosen mode and domains, or null while nothing is chosen. */
export function chosenWeb(choice: WebAccessChoice | null | undefined): WebChoice | null {
  if (!choice || !isMode(choice.mode)) return null;
  return { mode: choice.mode, domains: [...choice.domains] };
}

/** Caps from the preview's `limits` (null = no caps: every mode). */
export function webCapsFromLimits(limits: unknown): WebCaps {
  const web = limits && typeof limits === "object" ? (limits as Record<string, unknown>).web : null;
  if (!web || typeof web !== "object") return NO_CAPS;
  const w = web as Record<string, unknown>;
  const domains = strings(w.domains).map(normalizeDomain);
  const listed = Array.isArray(w.allow) ? WEB_MODES.filter((m) => (w.allow as unknown[]).includes(m)) : [...WEB_MODES];
  const allow = domains.length > 0 ? listed.filter((m) => m !== "public") : listed;
  return { allow, domains, suggested: strings(w.suggested_domains).map(normalizeDomain) };
}

export function normalizeDomain(value: string): string {
  return value.trim().toLowerCase().replace(/\.$/, "");
}

/** A chosen domain inside a cap entry: equal, or under a `.` entry (ADR-099 D-3). */
export function domainWithin(domain: string, cap: readonly string[]): boolean {
  const d = normalizeDomain(domain);
  return cap.some((raw) => {
    const entry = normalizeDomain(raw);
    if (d === entry) return true;
    return entry.startsWith(".") && (d === entry.slice(1) || d.endsWith(entry));
  });
}

/** Domains from "a.org, .b.org pypi.org": normalized, de-duplicated, checked. */
export function parseDomains(text: string): { domains: string[]; error: string | null } {
  const domains: string[] = [];
  for (const token of text.split(/[\s,]+/)) {
    const d = normalizeDomain(token);
    if (!d) continue;
    if (!DOMAIN_RE.test(d)) return { domains: [], error: `not a domain: ${token}` };
    if (!domains.includes(d)) domains.push(d);
  }
  if (domains.length > MAX_DOMAINS) return { domains: [], error: `at most ${MAX_DOMAINS} domains` };
  return { domains, error: null };
}

/** The answer as a choice, or the reason it cannot be used under the caps. */
export function validateWebChoice(
  mode: WebMode,
  domainsText: string,
  caps: WebCaps,
): { choice: WebChoice | null; error: string | null } {
  if (!caps.allow.includes(mode)) return { choice: null, error: `${WEB_MODE_TEXT[mode].label} is not allowed here` };
  if (mode !== "allowlist") return { choice: { mode, domains: [] }, error: null };
  const parsed = parseDomains(domainsText);
  if (parsed.error) return { choice: null, error: parsed.error };
  if (parsed.domains.length === 0) return { choice: null, error: "an allowlist needs at least one domain" };
  const outside = caps.domains.length ? parsed.domains.filter((d) => !domainWithin(d, caps.domains)) : [];
  if (outside.length) return { choice: null, error: `outside the allowed domains: ${outside.join(", ")}` };
  return { choice: { mode, domains: parsed.domains }, error: null };
}

/** The question's starting point: the current choice, else the remembered
 * one, else the first allowed mode; domains prefilled the same way, then
 * from the caps' suggestions. Preselected only — never applied unasked. */
export function webPromptDefaults(
  current: WebAccessChoice | null,
  remembered: WebAccessChoice | null,
  caps: WebCaps,
): { mode: WebMode; domainsText: string } {
  const seed = [chosenWeb(current), chosenWeb(remembered)].find((c) => c && caps.allow.includes(c.mode)) ?? null;
  const mode = seed?.mode ?? caps.allow[0] ?? "none";
  const withDomains = [chosenWeb(current), chosenWeb(remembered)].find((c) => c && c.domains.length);
  const domains = withDomains?.domains ?? caps.suggested;
  return { mode, domainsText: domains.join(", ") };
}

/** The user's own choice, or null. A local `default_off` (an `r4 -p` run
 * nobody chose for: web off, announced) is not a choice: a remote start
 * from it asks, and so does the console's first turn on it (ADR-099 D-2,
 * RS-1 round-3 fix N2). Mirrors r4's `remote_start_choice`. */
export function userWebChoice(choice: WebAccessChoice | null | undefined): WebChoice | null {
  if (!choice || choice.source === "default_off") return null;
  return chosenWeb(choice);
}

/** True when the console must ask before sending a turn: the bridge reported
 * no choice, or only the local `default_off` (null = a bridge that predates
 * RS-1: never ask), and the session is not a live remote one (its sandbox
 * already has the server's policy). */
export function needsWebChoice(choice: WebAccessChoice | null, venueState: string | undefined): boolean {
  if (!choice || userWebChoice(choice)) return false;
  return venueState !== "running" && venueState !== "provisioning";
}
