/**
 * Authentication manager — resolves Gateway credentials from:
 *   1. R4 CLI sources (~/.config/r4/sources.json) — primary credential store
 *   2. VS Code SecretStorage (manual login fallback)
 *   3. R4 CLI config (~/.config/r4/config.json profiles)
 *   4. Environment variables (R4_API_KEY, R4_GATEWAY_URL)
 *
 * The active CLI source's live api_key takes precedence over the
 * SecretStorage snapshot so that re-issued JWTs (e.g. a corrected
 * project_id) are picked up automatically and the extension stays in
 * sync with the `r4` CLI. SecretStorage is used only when no active
 * source api_key is available (manual login / env-only setups).
 *
 * On login, offers to import from existing CLI sources/profiles.
 */

import * as vscode from "vscode";
import * as fs from "fs";
import * as path from "path";
import * as os from "os";

const SECRET_KEY_TOKEN = "r4.gatewayToken";
const SECRET_KEY_ACTIVE_SOURCE = "r4.activeSource";

interface R4Source {
  name: string;
  kind?: string;
  url?: string;
  api_key?: string;
  gateway_url?: string;
  provider?: string;
  description?: string;
}

interface R4Sources {
  sources?: R4Source[];
  active?: string;
}

/** A source that can serve LLM traffic, resolved for client construction. */
export interface LlmSourceConfig {
  name: string;
  url: string;
  token: string;
  kind: "platform" | "public";
}

interface R4Profile {
  api_key?: string;
  gateway_url?: string;
  project_id?: string;
  model?: string;
}

interface R4Config {
  profiles?: Record<string, R4Profile>;
}

export class AuthManager {
  constructor(private readonly secrets: vscode.SecretStorage) {}

  // ── Token resolution (active source → SecretStorage → profile → env) ──

  /** Retrieve API token from best available source. */
  async getToken(): Promise<string | undefined> {
    // 1. R4 CLI sources.json (primary credential store). The live source
    //    api_key wins over the SecretStorage snapshot so re-issued JWTs
    //    (e.g. a corrected project_id) are picked up without a re-login,
    //    keeping the extension in sync with the `r4` CLI.
    const source = await this.getActiveCliSource();
    if (source?.api_key) {
      return source.api_key;
    }

    // 2. VS Code SecretStorage (manual login fallback)
    const stored = await this.secrets.get(SECRET_KEY_TOKEN);
    if (stored) {
      return stored;
    }

    // 3. R4 CLI config.json profile
    const profile = this.getActiveCliProfile();
    if (profile?.api_key) {
      return profile.api_key;
    }

    // 4. Environment variable
    return process.env.R4_API_KEY ?? undefined;
  }

  /** Store an API token securely. */
  async setToken(token: string): Promise<void> {
    await this.secrets.store(SECRET_KEY_TOKEN, token);
  }

  /** Delete the stored token. */
  async clearToken(): Promise<void> {
    await this.secrets.delete(SECRET_KEY_TOKEN);
  }

  // ── Gateway URL resolution (settings → CLI source → profile → env) ──

  /** Resolve Gateway URL from settings, CLI source, CLI profile, or env. */
  async getGatewayUrl(): Promise<string> {
    // 1. VS Code setting
    const configured = vscode.workspace
      .getConfiguration("r4")
      .get<string>("gatewayUrl", "");
    if (configured) {
      return configured;
    }

    // 2. R4 CLI sources.json
    const source = await this.getActiveCliSource();
    if (source?.url) {
      return source.url;
    }

    // 3. R4 CLI config.json profile
    const profile = this.getActiveCliProfile();
    if (profile?.gateway_url) {
      return profile.gateway_url;
    }

    // 4. Environment variable
    return process.env.R4_GATEWAY_URL ?? "";
  }

  /** Kind of the backend the resolved gateway URL points at.
   *
   * The active source may be a public OpenAI-compatible endpoint, which
   * serves ``/v1`` directly instead of the gateway's ``/gw/llm`` routes.
   * An explicit ``r4.gatewayUrl`` setting always denotes a platform gateway.
   */
  async getActiveSourceKind(): Promise<"platform" | "public"> {
    const configured = vscode.workspace
      .getConfiguration("r4")
      .get<string>("gatewayUrl", "");
    if (configured) {
      return "platform";
    }
    const source = await this.getActiveCliSource();
    return source?.kind === "public" ? "public" : "platform";
  }

  /** Check whether we have both URL and token from any source. */
  async isConfigured(): Promise<boolean> {
    const url = await this.getGatewayUrl();
    const token = await this.getToken();
    return Boolean(url) && Boolean(token);
  }

  // ── R4 CLI sources bridge ───────────────────────────────────

  /** Path to R4 config directory. */
  private getConfigDir(): string {
    return process.env.XDG_CONFIG_HOME
      ? path.join(process.env.XDG_CONFIG_HOME, "r4")
      : path.join(os.homedir(), ".config", "r4");
  }

  /** Read the R4 CLI sources.json (primary credential/source store). */
  private readCliSources(): R4Sources | undefined {
    const sourcesPath = path.join(this.getConfigDir(), "sources.json");
    try {
      const raw = fs.readFileSync(sourcesPath, "utf-8");
      return JSON.parse(raw) as R4Sources;
    } catch {
      return undefined;
    }
  }

  /** Get the VS Code-persisted active source name (SecretStorage). */
  async getActiveSourceName(): Promise<string | undefined> {
    try {
      return await this.secrets.get(SECRET_KEY_ACTIVE_SOURCE);
    } catch {
      return undefined;
    }
  }

  /** Synchronous fallback: read active source name from CLI sources.json.
   *  Used by callers that cannot await (e.g. label formatters). */
  getActiveSourceNameSync(): string {
    const sources = this.readCliSources();
    if (!sources?.sources || sources.sources.length === 0) {
      return "R4";
    }
    // Use CLI sources.active (the file persisted preference)
    if (sources.active) {
      const match = sources.sources.find((s) => s.name === sources.active);
      if (match) { return match.name; }
    }
    // First platform source
    const platform = sources.sources.find((s) => s.kind === "platform");
    if (platform) { return platform.name; }
    return sources.sources[0].name;
  }

  /** Persist the active source name so it survives restarts. */
  private async setActiveSourceName(name: string): Promise<void> {
    await this.secrets.store(SECRET_KEY_ACTIVE_SOURCE, name);
  }

  /** Get the active CLI source entry.
   *
   * The CLI `sources.json` `active` field is the single, shared "active
   * deployment" pointer used by every R4 client (the `r4` CLI, the Python
   * companion, and this extension). It is authoritative here so that
   * switching deployments in one place (e.g. `r4 source use rccs`) keeps all
   * clients in sync and no client can silently pin a retired deployment.
   *
   * Resolution order:
   *   1. Explicit VS Code `r4.profile` setting (deliberate per-workspace
   *      override).
   *   2. CLI `sources.active` (shared source of truth).
   *   3. VS Code SecretStorage snapshot (fallback only — reconciled against
   *      the live source list so a stale name can never win).
   *   4. First source.
   */
  async getActiveCliSource(): Promise<R4Source | undefined> {
    const sources = this.readCliSources();
    if (!sources?.sources || sources.sources.length === 0) {
      return undefined;
    }

    // 1. Explicit VS Code profile setting (deliberate override).
    const profileName = this.getActiveProfileName();
    if (profileName) {
      const byProfile = sources.sources.find((s) => s.name === profileName);
      if (byProfile) {
        return byProfile;
      }
    }

    // 2. CLI sources.active — the shared source of truth.
    if (sources.active) {
      const active = sources.sources.find((s) => s.name === sources.active);
      if (active) {
        return active;
      }
    }

    // 3. VS Code persisted source name (fallback; reconciled against the live
    //    source list, so a name for a source that no longer exists is ignored).
    const persistedName = await this.getActiveSourceName();
    if (persistedName) {
      const byName = sources.sources.find((s) => s.name === persistedName);
      if (byName) {
        return byName;
      }
    }

    // 4. First source.
    return sources.sources[0];
  }

  /** List all available CLI sources. */
  listCliSources(): R4Source[] {
    return this.readCliSources()?.sources ?? [];
  }

  /**
   * Add or update a source in the R4 CLI `sources.json` (shared store).
   *
   * Mirrors `r4 source add` semantics: replace an existing entry with the
   * same name, auto-activate when it is the first registered source, and
   * persist with user-only permissions. The CLI, companion TUI, and this
   * extension all read the same file, so the new source appears in every
   * R4 client without further wiring.
   *
   * Returns { updated: boolean (replaced an existing entry), activated: boolean }.
   */
  upsertCliSource(source: R4Source): { updated: boolean; activated: boolean } {
    const sourcesPath = path.join(this.getConfigDir(), "sources.json");
    const data = this.readCliSources() ?? {};
    const existing = data.sources ?? [];
    const updated = existing.some((s) => s.name === source.name);
    data.sources = [...existing.filter((s) => s.name !== source.name), source];
    const activated = !data.active;
    if (activated) {
      data.active = source.name;
    }
    fs.mkdirSync(path.dirname(sourcesPath), { recursive: true });
    fs.writeFileSync(sourcesPath, `${JSON.stringify(data, null, 2)}\n`, {
      encoding: "utf-8",
      mode: 0o600,
    });
    return { updated, activated };
  }

  /** List only platform sources (filtered from CLI sources.json). */
  listPlatformSources(): R4Source[] {
    return this.listCliSources().filter((s) => s.kind === "platform");
  }

  /**
   * List platform sources with resolved url+token, ready to build a
   * GatewayClient per source for multi-source polling. Only sources that
   * have both a url and an api_key are returned.
   */
  listPlatformSourceConfigs(): { name: string; url: string; token: string }[] {
    return this.listPlatformSources()
      .filter((s) => s.url && s.api_key)
      .map((s) => ({ name: s.name, url: s.url as string, token: s.api_key as string }));
  }

  /**
   * List every source that can serve LLM traffic — platform gateways *and*
   * public OpenAI-compatible endpoints.
   *
   * Platform sources need an api_key (the gateway rejects anonymous calls);
   * public endpoints may legitimately be unauthenticated (e.g. a local
   * Ollama), so only a url is required there.
   */
  listLlmSourceConfigs(): LlmSourceConfig[] {
    return this.listCliSources()
      .filter((s) => s.url && (s.kind === "public" || s.api_key))
      .map((s) => ({
        name: s.name,
        url: s.url as string,
        token: s.api_key ?? "",
        kind: s.kind === "public" ? ("public" as const) : ("platform" as const),
      }));
  }

  /** Get a specific source by name. */
  getSourceForName(name: string): R4Source | undefined {
    return this.listCliSources().find((s) => s.name === name);
  }

  /** Switch the active source and persist the choice.
   *
   * Writes through to the CLI `sources.json` `active` field so the choice
   * propagates to every R4 client (CLI, companion), keeping the shared
   * "active deployment" pointer consistent. The SecretStorage snapshot is
   * kept only as a fallback for setups without a writable sources.json.
   */
  async switchSource(sourceName: string): Promise<R4Source | undefined> {
    const source = this.getSourceForName(sourceName);
    if (!source) {
      vscode.window.showErrorMessage(`R4: Source '${sourceName}' not found.`);
      return undefined;
    }
    this.writeCliActiveSource(sourceName);
    await this.setActiveSourceName(sourceName);
    vscode.window.showInformationMessage(`R4: Switched to source '${sourceName}'`);
    return source;
  }

  /** Update the CLI `sources.json` `active` field (shared deployment pointer).
   *  Best-effort: preserves all other fields and never throws. */
  private writeCliActiveSource(name: string): void {
    const sourcesPath = path.join(this.getConfigDir(), "sources.json");
    try {
      const raw = fs.readFileSync(sourcesPath, "utf-8");
      const data = JSON.parse(raw) as R4Sources;
      if (!data.sources?.some((s) => s.name === name)) {
        return;
      }
      data.active = name;
      fs.writeFileSync(sourcesPath, `${JSON.stringify(data, null, 2)}\n`, "utf-8");
    } catch {
      // sources.json missing or unwritable — SecretStorage fallback still applies.
    }
  }

  // ── R4 CLI config bridge ──────────────────────────────────

  /** Read the R4 CLI config.json. */
  private readCliConfig(): R4Config | undefined {
    const configPath = path.join(this.getConfigDir(), "config.json");
    try {
      const raw = fs.readFileSync(configPath, "utf-8");
      return JSON.parse(raw) as R4Config;
    } catch {
      return undefined;
    }
  }

  /** Get the active CLI profile name. */
  private getActiveProfileName(): string {
    // VS Code setting → env → "default"
    const fromSetting = vscode.workspace
      .getConfiguration("r4")
      .get<string>("profile", "");
    if (fromSetting) {
      return fromSetting;
    }
    return process.env.R4_PROFILE ?? "default";
  }

  /** Get the active CLI profile data. */
  private getActiveCliProfile(): R4Profile | undefined {
    const config = this.readCliConfig();
    if (!config?.profiles) {
      return undefined;
    }
    const name = this.getActiveProfileName();
    return config.profiles[name];
  }

  /** List all available CLI profiles. */
  listCliProfiles(): { name: string; profile: R4Profile }[] {
    const config = this.readCliConfig();
    if (!config?.profiles) {
      return [];
    }
    return Object.entries(config.profiles).map(([name, profile]) => ({
      name,
      profile,
    }));
  }

  // ── Interactive login ─────────────────────────────────────

  /** Interactive login: import from CLI profile or manual entry. */
  async login(): Promise<boolean> {
    // Check for existing CLI sources (primary) and profiles
    const sources = this.listCliSources().filter((s) => s.api_key && (s.url || s.gateway_url));
    const profiles = this.listCliProfiles().filter(
      (p) => p.profile.api_key && p.profile.gateway_url,
    );

    const hasCredentials = sources.length > 0 || profiles.length > 0;

    if (hasCredentials) {
      const result = await loginFromCredentialPicker(this, sources, profiles);
      if (result !== undefined) {
        return result;
      }
      // User chose manual entry — fall through
    }

    return loginManual(this);
  }

  /** Interactive logout: clear token. */
  async logout(): Promise<void> {
    await this.clearToken();
    vscode.window.showInformationMessage("R4: Logged out.");
  }

  // ── JWT role detection ──────────────────────────────────────

  /** Decode the `roles` array from a JWT payload. */
  decodeTokenRoles(token: string): string[] {
    return decodeTokenRoles(token);
  }

  /** Check whether the current logged-in user is an admin. */
  async isAdmin(): Promise<boolean> {
    const token = await this.getToken();
    if (!token) { return false; }
    const roles = this.decodeTokenRoles(token);
    return roles.includes("admin");
  }

  /** Resolve the account name (JWT `sub` claim) for the active source.
   *
   *  Used to show `R4: Connected (source:account)` in the status bar and
   *  in the source-switch QuickPick. Falls back to `undefined` when the
   *  token is not a JWT or lacks a `sub` claim.
   */
  async getAccountName(): Promise<string | undefined> {
    const token = await this.getToken();
    if (!token) { return undefined; }
    return decodeTokenSubject(token);
  }
}

// ── Login helpers (standalone for lizard compatibility) ───────

/** Show quick-pick for existing credentials. Returns undefined when user picks manual entry. */
async function loginFromCredentialPicker(
  auth: AuthManager,
  sources: R4Source[],
  profiles: { name: string; profile: R4Profile }[],
): Promise<boolean | undefined> {
  const items: vscode.QuickPickItem[] = [
    ...sources.map((s) => ({
      label: `$(key) ${s.name}`,
      description: s.url ?? s.gateway_url ?? "",
      detail: s.kind ? `Source (${s.kind})` : "Source",
    })),
    ...profiles
      .filter((p) => !sources.some((s) => s.name === p.name))
      .map((p) => ({
        label: `$(key) ${p.name}`,
        description: p.profile.gateway_url ?? "",
        detail: p.profile.project_id
          ? `Profile — Project: ${p.profile.project_id}`
          : "Profile",
      })),
    { label: "$(edit) Enter credentials manually", description: "" },
  ];

  const picked = await vscode.window.showQuickPick(items, {
    title: "R4: Login — select credential source",
    placeHolder: "Import from CLI or enter manually",
  });

  if (!picked) {
    return false;
  }
  if (picked.label.startsWith("$(edit)")) {
    return undefined; // manual entry
  }

  const credName = picked.label.replace("$(key) ", "");
  return importCredential(auth, credName, sources, profiles);
}

/** Import credential from a CLI source or profile by name. */
async function importCredential(
  auth: AuthManager,
  name: string,
  sources: R4Source[],
  profiles: { name: string; profile: R4Profile }[],
): Promise<boolean> {
  const src = sources.find((s) => s.name === name);
  if (src) {
    await auth.setToken(src.api_key!);
    await maybeSetGatewayUrl(src.url ?? src.gateway_url);
    return true;
  }

  const selected = profiles.find((p) => p.name === name);
  if (!selected) {
    return false;
  }
  await auth.setToken(selected.profile.api_key!);
  await maybeSetGatewayUrl(selected.profile.gateway_url);
  await maybeSetDefaultModel(selected.profile.model);
  return true;
}

/** Set gateway URL in settings if not already configured. */
async function maybeSetGatewayUrl(url?: string): Promise<void> {
  if (!url) { return; }
  const current = vscode.workspace.getConfiguration("r4").get<string>("gatewayUrl", "");
  if (!current) {
    await vscode.workspace.getConfiguration("r4")
      .update("gatewayUrl", url, vscode.ConfigurationTarget.Global);
  }
}

/** Set default model in settings if not already configured. */
async function maybeSetDefaultModel(model?: string): Promise<void> {
  if (!model) { return; }
  const current = vscode.workspace.getConfiguration("r4").get<string>("defaultModel", "");
  if (!current) {
    await vscode.workspace.getConfiguration("r4")
      .update("defaultModel", model, vscode.ConfigurationTarget.Global);
  }
}

/** Manual credential entry fallback.
 *
 * Asks for the JWT token first.  If the token payload carries a
 * ``gateway_url`` claim (as tokens generated by ``umrs-server admin
 * token generate-jwt --gateway-url`` do), the Gateway URL is
 * auto-configured from the JWT and the URL prompt is skipped entirely.
 * Falls back to the existing URL resolution chain (settings → CLI
 * sources → env) only when the JWT does not embed a ``gateway_url``.
 */
/** Path to the r4 CLI when it is on PATH. Empty string when absent. */
async function resolveR4CliPath(): Promise<string> {
  const { execFile } = await import("node:child_process");
  return new Promise((resolve) => {
    execFile("which", ["r4"], (err, stdout) => {
      if (err) {
        resolve("");
      } else {
        resolve(stdout.trim());
      }
    });
  });
}

/** Fire-and-forget wrapper: run `r4 auth login` to hydrate the canonical
 * store. Returns ``true`` on success, ``false`` on cancellation, and
 * ``undefined`` when the legacy flow should run instead.
 *
 * The existing extension flow wrote tokens only to VS Code SecretStorage,
 * which left fresh installs stuck — manual login never hydrated
 * sources.json/profile entries, so nothing else (companion CLI, future
 * source discovery, terminal integration) saw the credential.  Delegating
 * manual login to the `r4` CLI makes the extension-first experience
 * complete on its own.
 */
async function loginViaR4Cli(auth: AuthManager): Promise<boolean | undefined> {
  const cliPath = await resolveR4CliPath();
  if (!cliPath) {
    return undefined; // No r4 CLI installed — fall back to direct SecretStorage.
  }

  const choice = await vscode.window.showInformationMessage(
    "R4: How would you like to log in?",
    { modal: true, detail: "Using the r4 CLI configures sources.json and your profile so terminal + extension stay in sync." },
    "Use r4 CLI (Recommended)",
    "Enter token manually",
  );
  if (choice !== "Use r4 CLI (Recommended)") {
    // User picked manual entry (or Escape) — run the legacy flow.
    return choice === "Enter token manually" ? undefined : false;
  }

  // Gather the inputs the CLI needs, then delegate.
  const gatewayUrl = await vscode.window.showInputBox({
    title: "R4 Gateway URL",
    prompt: "Enter your Gateway URL (e.g. https://data0.ai.r-ccs.riken.jp/rccs)",
    placeHolder: "https://...",
    value: (await auth.getGatewayUrl()) ?? "",
    ignoreFocusOut: true,
  });
  if (!gatewayUrl) {
    return false;
  }

  const apiKey = await vscode.window.showInputBox({
    title: "R4 API Token",
    prompt: "Enter your R4 / Gateway API token (JWT)",
    password: true,
    ignoreFocusOut: true,
  });
  if (!apiKey) {
    return false;
  }

  const profile = await vscode.window.showInputBox({
    title: "R4 Profile",
    prompt: "Profile name for these credentials (leave blank for 'default')",
    value: "default",
    ignoreFocusOut: true,
  });
  if (profile === undefined) {
    return false;
  }

  const args = ["auth", "login", "--gateway-url", gatewayUrl, "--api-key", apiKey];
  if (profile.trim()) {
    args.push("--profile", profile.trim());
  }

  const { execFile } = await import("node:child_process");
  const exitCode = await new Promise<number>((resolve) => {
    execFile(cliPath, args, { timeout: 120_000, maxBuffer: 1_000_000 }, (err) => {
      resolve(err ? (err as NodeJS.ErrnoException & { code?: number }).code ?? 1 : 0 as number);
    });
  });

  if (exitCode !== 0) {
    const retry = await vscode.window.showErrorMessage(
      "R4: `r4 auth login` failed. Would you like to enter the token manually instead?",
      "Enter manually",
      "Retry",
      "Cancel",
    );
    if (retry === "Enter manually") {
      return undefined;
    }
    if (retry === "Retry") {
      return loginViaR4Cli(auth);
    }
    return false;
  }

  // r4 has already written sources.json, the profile, and SecretStorage
  // (via the CLI). Mirror the token into SecretStorage so deployments
  // without an active CLI source still resolve auth.
  await auth.setToken(apiKey);
  vscode.window.showInformationMessage(`R4: Logged in via \`r4 auth login\` (profile: ${profile.trim() || "default"}).`);
  return true;
}

async function loginManual(auth: AuthManager): Promise<boolean> {
  // Primary path: delegate to `r4 auth login` so manual login hydrates the
  // canonical store (sources.json + profile + secrets). The extension's
  // prior behavior wrote only to SecretStorage, which made fresh installs
  // dependent on `r4 auth login` running in a terminal first.
  //
  // Falls back to the legacy SecretStorage-only flow when:
  //   1. The r4 CLI is not installed.
  //   2. The user explicitly picked the manual path at the prompt.
  //   3. The r4 login process failed non-fatally.
  const delegated = await loginViaR4Cli(auth);
  if (delegated !== undefined) {
    return delegated;
  }

  // ── 1. Token (always required) ──
  const token = await vscode.window.showInputBox({
    title: "R4 API Token",
    prompt: "Enter your R4 / Gateway API token (JWT)",
    password: true,
  });
  if (!token) {
    return false;
  }

  // ── 2. Extract gateway_url from JWT payload if present ──
  const jwtUrl = decodeTokenGatewayUrl(token);
  if (jwtUrl) {
    // JWT carries the gateway URL — set it and skip the URL prompt.
    await vscode.workspace
      .getConfiguration("r4")
      .update("gatewayUrl", jwtUrl, vscode.ConfigurationTarget.Global);
    await auth.setToken(token);
    return true;
  }

  // ── 3. No gateway_url in JWT — resolve via existing chain ──
  let url = await auth.getGatewayUrl();
  if (!url) {
    url =
      (await vscode.window.showInputBox({
        title: "R4 Gateway URL",
        prompt:
          "Enter the Gateway URL (e.g. http://gateway.example.com:8000)",
        placeHolder: "http://...",
      })) ?? "";
    if (!url) {
      return false;
    }
    await vscode.workspace
      .getConfiguration("r4")
      .update("gatewayUrl", url, vscode.ConfigurationTarget.Global);
  }

  await auth.setToken(token);
  return true;
}

// ── JWT helpers ────────────────────────────────────────────────

/** Decode the full JWT payload as a key-value object. Returns empty object on failure. */
function decodeTokenPayload(token: string): Record<string, unknown> {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) { return {}; }
    // Handle base64url → base64
    const b64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    const decoded = Buffer.from(b64, "base64").toString("utf-8");
    return JSON.parse(decoded) as Record<string, unknown>;
  } catch {
    return {};
  }
}

/**
 * Base64-decode the JWT payload and return the `roles` array.
 * Returns empty array on parse failure or missing roles.
 */
function decodeTokenRoles(token: string): string[] {
  const obj = decodeTokenPayload(token);
  const roles = obj.roles;
  if (Array.isArray(roles)) {
    return roles.filter((r): r is string => typeof r === "string");
  }
  return [];
}

/** Extract gateway_url from JWT payload. Returns undefined if not present. */
function decodeTokenGatewayUrl(token: string): string | undefined {
  const obj = decodeTokenPayload(token);
  const url = obj.gateway_url;
  return typeof url === "string" ? url : undefined;
}

/** Extract the `sub` (account/user identifier) claim from a JWT.
 *  Returns undefined when the token is malformed or has no `sub`. */
function decodeTokenSubject(token: string): string | undefined {
  const obj = decodeTokenPayload(token);
  const sub = obj.sub;
  return typeof sub === "string" ? sub : undefined;
}
