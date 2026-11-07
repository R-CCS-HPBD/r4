/**
 * The session's local tool sandbox (ADR-101) — pure helpers, bridge-free.
 *
 * Off by default (D-1, like Claude Code): the console tells the user once a
 * local session opens, and how to turn it on (`/sandbox on`). The bridge owns
 * the words: `session.sandbox.notice` (the off or unavailable line),
 * `mcp_error` (why local MCP servers did not follow) and `detail` (what
 * turning it on confines). This module only frames them and adds the status
 * chip and the `/sandbox status` text (2026-10-30, review Q-5: the console's
 * own copy of the detail lines had drifted from the bridge's).
 * A frontend (remote) session runs in the server's podman sandbox
 * (ADR-046) and gets none of this; a bridge that predates the key neither.
 * The Python side is `r4/src/r4/companion/ui/sandbox_bridge.py`.
 */

import type { ChipState } from "./chip-state";

/** `snapshot.session.sandbox`, mapped (rpc `SandboxState`). */
export interface SandboxView {
  mode: "off" | "on" | "remote";
  source: string;
  backend: string;
  available: boolean;
  reason: string;
  tmpDir: string;
  writeRoots: string[];
  network: string;
  notice: string;
  /** Why local MCP servers did not follow an on sandbox; "" when they did. */
  mcpError: string;
  /** What turning the sandbox on confines (the bridge's SANDBOX_DETAIL_LINES). */
  detail: string[];
}

/** The keyed system row the session-start notice and `/sandbox on|off` share. */
export const SANDBOX_NOTICE_KEY = "sandbox-notice";

export const SANDBOX_USAGE = "usage: /sandbox [status|on|off|default on|default off]";

const MODES = new Set(["off", "on", "remote"]);

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function textList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => String(v)).filter((v) => v.trim() !== "") : [];
}

/** Bridge `sandbox` → view; null when the payload does not carry one (old bridge). */
export function mapSandbox(raw: unknown): SandboxView | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  const mode = text(r.mode);
  return {
    mode: MODES.has(mode) ? (mode as SandboxView["mode"]) : "off",
    source: text(r.source),
    backend: text(r.backend),
    available: r.available !== false,
    reason: text(r.reason),
    tmpDir: text(r.tmp_dir),
    writeRoots: textList(r.write_roots),
    network: text(r.network),
    notice: text(r.notice).trim(),
    mcpError: text(r.mcp_error).trim(),
    detail: textList(r.detail),
  };
}

/** True when the console shows sandbox chrome for this session at all. */
export function sandboxApplies(state: SandboxView | null | undefined, venue: string | undefined): state is SandboxView {
  return Boolean(state) && venue !== "frontend" && state!.mode !== "remote";
}

/** "bwrap" · "seatbelt" · "no backend". */
function backendName(state: SandboxView): string {
  return state.backend || "no backend";
}

/** The short line the keyed row shows once the sandbox is on. */
export function sandboxOnLine(state: SandboxView): string {
  return `✓ Sandbox on (${backendName(state)}): writes only to the workspace and a session temp dir; network only through r4's proxy · /sandbox status`;
}

function hasNotice(state: SandboxView): boolean {
  return Boolean(state.notice || state.mcpError);
}

/** The keyed row's text for a bridge notice: the bridge's words, framed. */
function framedNotice(state: SandboxView): string {
  const mcp = state.mcpError ? `MCP: ${state.mcpError}` : "";
  if (!state.notice) return `⚠ Sandbox on (${backendName(state)}) · ${mcp}`;
  return mcp ? `⚠ ${state.notice} · ${mcp}` : `⚠ ${state.notice}`;
}

/** Session start (init_state, new session, resume): the row, or "" for none. */
export function sandboxStartText(state: SandboxView | null | undefined, venue: string | undefined): string {
  if (!sandboxApplies(state, venue) || !hasNotice(state)) return "";
  return framedNotice(state);
}

/** After `/sandbox on|off`: the notice when the bridge has one, else the on line. */
export function sandboxRowText(state: SandboxView): string {
  if (hasNotice(state)) return framedNotice(state);
  return state.mode === "on" ? sandboxOnLine(state) : "";
}

/** The expandable detail lines under the keyed row (Enter on an empty draft):
 * the bridge's own lines, as sent, and the backend. */
export function sandboxDetailLines(state: SandboxView): string[] {
  const backend = state.available
    ? `backend: ${backendName(state)}`
    : `backend: unavailable (${state.reason || "no sandbox backend on this system"})`;
  return [
    "/sandbox on confines the agent's shell, Python and local MCP servers:",
    ...state.detail.map((line) => `  ${line}`),
    backend,
    "/sandbox status shows the details",
  ];
}

const CHIPS: Record<"on" | "off" | "n/a", { label: string; state: ChipState }> = {
  on: { label: "sbx:on", state: "ok" },
  off: { label: "sbx:off", state: "warn" },
  "n/a": { label: "sbx:n/a", state: "error" },
};

/** Status-bar chip: sbx:on (ok) · sbx:off (warn) · sbx:n/a (error, no backend);
 * null for a frontend session or a bridge without the key (never made up). */
export function sandboxChip(
  state: SandboxView | null | undefined,
  venue: string | undefined,
): { label: string; state: ChipState } | null {
  if (!sandboxApplies(state, venue)) return null;
  if (!state.available) return CHIPS["n/a"];
  return state.mode === "on" ? CHIPS.on : CHIPS.off;
}

function statusHead(state: SandboxView): string {
  return `sandbox: ${state.mode}${state.source ? ` (source: ${state.source})` : ""}`;
}

/** A frontend session (`remote_state()`: no local backend, no write roots,
 * no tmp dir): the server's sandbox, not a misleading "no backend" (tui-lane
 * review 2026-10-30). */
function remoteStatusText(state: SandboxView): string {
  const lines = [statusHead(state), "  backend: server sandbox (ADR-046)", `  network: ${state.network || "—"}`];
  if (state.reason) lines.push(`  ${state.reason}`);
  return lines.join("\n");
}

/** `/sandbox status`: mode, source, backend, write roots, network, tmp dir. */
export function sandboxStatusText(state: SandboxView): string {
  if (state.mode === "remote") return remoteStatusText(state);
  const backend = state.available ? backendName(state) : `unavailable — ${state.reason || "no backend"}`;
  const lines = [
    statusHead(state),
    `  backend: ${backend}`,
    `  writes: ${state.writeRoots.join(", ") || "—"}`,
    `  network: ${state.network || "—"}`,
    `  tmp dir: ${state.tmpDir || "—"}`,
  ];
  if (state.notice) lines.push(`  ${state.notice}`);
  if (state.mcpError) lines.push(`  ⚠ MCP: ${state.mcpError}`);
  return lines.join("\n");
}

/** `session.sandbox_set`'s reply, as the lines `/sandbox on|off` posts. */
export interface SandboxSetReply {
  sandbox: SandboxView;
  restartedMcp: string[];
  stillRunning: string[];
  mcpError: string;
}

const HEADS: Record<"on" | "off", (state: SandboxView) => string> = {
  on: (state) => `sandbox on (${backendName(state)}) — applies from the agent's next command`,
  off: () => "sandbox off — the agent's next commands run with your full permissions",
  // "remote" never answers a set (refused with sandbox.remote).
};

/** The notice `/sandbox on|off` posts after a successful set. */
export function sandboxSetText(reply: SandboxSetReply, asDefault: boolean): string {
  const mode = reply.sandbox.mode === "on" ? "on" : "off";
  const lines = [HEADS[mode](reply.sandbox) + (asDefault ? ` · default for new sessions: ${mode}` : "")];
  if (reply.restartedMcp.length) lines.push(`MCP servers restarted under the sandbox: ${reply.restartedMcp.join(", ")}`);
  if (reply.mcpError) lines.push(`⚠ MCP: ${reply.mcpError}`);
  if (reply.stillRunning.length) {
    lines.push(`still running without the sandbox: ${reply.stillRunning.join(", ")}; stop them if needed`);
  }
  return lines.join("\n");
}
