/**
 * Temporal selectable list overlay (W2.1) — skills / MCP servers / UMRS workflows.
 *
 * Opened by slash commands (/skills, /mcp, /workflow) instead of printing
 * static lines into the chat transcript. Esc or done closes; rows are
 * selectable with ↑↓ (mouse too); Enter applies the row's primary action.
 */

import { For, Show, createEffect, createMemo, createSignal, on, onMount } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { useTerminalDims } from "../lib/terminal-dims";
import { SideBySideDiff } from "../ui/SideBySideDiff";
import { localTimeHHMMSS } from "../lib/time";
import { fetchGovernanceOverview, fetchSessionChanges } from "../lib/session-changes";
import { workspace, actions, activeTab } from "../stores";
import { palette } from "../themes";
import { KeyTrap } from "./key-trap";
import { handleListKey, clampIndex } from "../lib/selectable-list";
import { skillNoticeText, mcpServerNoticeText } from "../lib/inventory-notice";
import { getCompanionBridge } from "../bridge";
import { isOfflineBackend } from "../lib/console-mode";
import type { McpServerEntry } from "../lib/mcp-report";
import { executionRowDetail, umrsWorkflowActions, umrsWorkflowStore } from "../stores/umrs-workflow";
import { progressSuffix } from "../lib/workflow-activity";
import { waitingApprovalStepName } from "../lib/workflow-graph-projection";
import { catalogRowLabel, runLabelWithDigest } from "../lib/workflow-versions";
import { truncate } from "../lib/text";
import { mergeSkillInventories, visibleSkillRows } from "../lib/skill-inventory";
import { Floating } from "../ui/Floating";
import { sourceRowLabel, type SourceHealth } from "../lib/source-health";

interface OverlayRow {
  key: string;
  label: string;
  detail?: string | null;
  status?: string;
  /** Present when the row carries an on/off state: ● on (green) / ○ off (dim). */
  toggle?: boolean;
}

/** A revert of a file the session created deletes it (tools.revert_session_change
 * returns deleted=true); "reverted → before-state" hid that (TUI audit 2026-10-17). */
export function revertNoticeText(path: string, result: unknown): string {
  const deleted = Boolean(result && typeof result === "object" && (result as { deleted?: unknown }).deleted === true);
  return deleted ? `removed ${path} (created this session)` : `reverted ${path} → session before-state`;
}

/** Unique overlay-row key for an MCP server: local (user-config) and registry
 * rows may share a NAME once a local server is registered — keying by name
 * alone made the registry row shadow the toggleable local row (on/off chip
 * "disappeared", Enter fell back to the read-only detail notice). */
export function mcpRowKey(server: McpServerEntry): string {
  const name = server.name ?? server.server_id ?? "?";
  return String(server.source ?? "").startsWith("user-config")
    ? `local:${name}`
    : `reg:${server.server_id ?? name}`;
}

type OverlayKind =
  | "skills"
  | "mcp"
  | "workflow"
  | "changes"
  | "governance"
  | "jobgraph"
  | "sources"
  | "connect";

interface JobGraphState {
  loading: boolean;
  error: string | null;
  jobId: string | null;
  state: string;
  attempts: Array<Record<string, unknown>>;
}

/** Source rows for the source-switch overlay. */
interface SourceState {
  loading: boolean;
  error: string | null;
  rows: Array<{
    name: string;
    kind: string;
    url: string;
    active: boolean;
    has_api_key?: boolean;
    description?: string | null;
    provider?: string | null;
    /** R1/R2 sealed-remote badge when fetched with --remote (with_leases projection). */
    remote?: string;
  }>;
}

async function ensureMcpInventory(): Promise<void> {
  if (workspace.mcpServers !== null || isOfflineBackend()) return;
  try {
    const res = (await getCompanionBridge().request("list_mcp_servers", {}, 15_000)) as {
      servers?: McpServerEntry[];
    };
    actions.setMcpServers(res.servers ?? []);
  } catch {
    actions.setMcpServers([]);
  }
}

/** Bridge-authoritative skill inventory (has active flags + tools).
 * Also pulls the LOCAL CLI scan up front: without it, local rows only arrive
 * on the next background poll — mid-interaction the list visibly grows
 * ("elements and length change every time I click"). */
async function ensureSkillsInventory(): Promise<void> {
  if (isOfflineBackend()) return;
  const { listCliSkills } = await import("../adapters/r4-cli");
  const cliSkills = await listCliSkills();
  if (cliSkills.length > 0) actions.setBackendState({ skills: cliSkills });
  try {
    const res = (await getCompanionBridge().request("list_skills", {}, 15_000)) as {
      skills?: Array<Record<string, unknown>>;
    };
    if (Array.isArray(res.skills)) {
      const { mapSkill } = await import("../adapters/mappers");
      actions.setSkillInventory(res.skills.map((s) => mapSkill(s)));
    }
  } catch {
    // bridge unavailable — overlay falls back to the merged poll inventory
  }
}

/** RPC payload for one activation toggle (server default is active: true!). */
export function skillActivationParams(name: string, active: boolean): { name: string; active: boolean } {
  return { name, active };
}

/** Enter: toggle activation for the row's skill via the bridge.
 * Server default is active=true — without an explicit `active` the call can
 * never deactivate (the "activation works, deactivation impossible" bug). */
async function toggleSkillActivation(name: string, active: boolean): Promise<void> {
  try {
    const res = (await getCompanionBridge().request(
      "activate_skill",
      skillActivationParams(name, active),
      15_000,
    )) as { skills?: Array<Record<string, unknown>> };
    if (res && Array.isArray(res.skills)) {
      const { mapSkill } = await import("../adapters/mappers");
      actions.setSkillInventory(res.skills.map((s) => mapSkill(s)));
      const after = res.skills.find((s) => s.name === name);
      const state = after && typeof after.active === "boolean" ? after.active : undefined;
      actions.sendSystemNotice(
        state === undefined
          ? `skill toggled: ${name}`
          : `skill ${state ? "activated" : "deactivated"}: ${name}`,
      );
    }
  } catch (err) {
    actions.sendSystemNotice(
      `skill toggle failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

/** EXPLANATIONS: config description → hint → url/command target. */
export function mcpExplanation(server: McpServerEntry): string | null {
  const desc = server.description?.trim();
  if (desc) return desc;
  if (server.hint) return server.hint;
  return server.url ?? null;
}

export interface McpExposure {
  proxy?: { state?: string; reason?: string; port?: number };
  registration?: { state?: string; reason?: string; server_id?: string };
}

/** Outcome tail for the toggle notice — names the exact blocker. */
export function exposureNoticeTail(exposure: McpExposure | undefined): string {
  if (!exposure) return "";
  const proxy = exposure.proxy?.state ?? "?";
  if (proxy === "failed") {
    return ` · proxy failed: ${exposure.proxy?.reason ?? "unknown"}`;
  }
  const reg = exposure.registration ?? {};
  if (reg.state === "registered") {
    return ` · proxy ${proxy} :${exposure.proxy?.port ?? 50800} · registered ✓ (agent-visible next session or mcp_refresh)`;
  }
  return ` · proxy ${proxy} · registration blocked: ${reg.reason ?? "unknown"}`;
}

/** Enter on a user-config row: flip its enabled flag via the bridge (atomic
 * config rewrite); ON also ensures the proxy runs and registers the name. */
async function toggleMcpServerEnabled(server: McpServerEntry): Promise<void> {
  const name = server.name ?? server.server_id ?? "";
  const enabled = server.enabled === false;
  try {
    const res = (await getCompanionBridge().request(
      "set_mcp_server_enabled",
      { name, enabled },
      30_000,
    )) as {
      servers?: McpServerEntry[];
      applied?: boolean;
      reason?: string;
      exposure?: McpExposure;
    };
    if (res.applied === false) {
      actions.sendSystemNotice(`mcp toggle failed: ${res.reason ?? "unknown"}`);
      return;
    }
    actions.setMcpServers(res.servers ?? []);
    actions.sendSystemNotice(`mcp ${name} ${enabled ? "on" : "off"}${exposureNoticeTail(res.exposure)}`);
  } catch (err) {
    actions.sendSystemNotice(
      `mcp toggle failed: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

function statusGlyph(status?: string): string {
  const s = String(status ?? "").toLowerCase();
  if (["running", "started", "submitted"].includes(s)) return "◐";
  if (["succeeded", "completed", "done"].includes(s)) return "✓";
  if (["failed", "error", "cancelled"].includes(s)) return "✗";
  return "•";
}

type AnyRecord = Record<string, unknown>;

function ownershipRows(ownership: unknown): OverlayRow[] {
  const block = ownership as { entries?: AnyRecord[]; ownership?: AnyRecord[] } | undefined;
  const items = block?.entries ?? block?.ownership ?? [];
  if (!Array.isArray(items)) return [];
  return items.map((entry) => {
    const type = String(entry.resource_type ?? entry.type ?? "?");
    const id = String(entry.resource_id ?? entry.id ?? "?");
    const owner = String(entry.owner_id ?? entry.owner ?? "?");
    const tier = String(entry.tier ?? "");
    return {
      key: `own:${type}:${id}`,
      label: `◆ ${type}/${id} · owner ${owner}${tier ? ` [${tier}]` : ""}`,
      detail: null,
    };
  });
}

function quotaRows(quotas: unknown): OverlayRow[] {
  const items = (quotas as { quotas?: AnyRecord[] } | undefined)?.quotas ?? [];
  if (!Array.isArray(items)) return [];
  return items.map((q) => {
    const proj = String(q.project_id ?? "?");
    const type = String(q.resource_type ?? "?");
    const current = Number(q.current_count ?? 0);
    const max = Number(q.max_count ?? 0);
    const usage = max > 0 ? Math.min(1, current / max) : 0;
    const filled = bar(usage);
    return {
      key: `quota:${proj}:${type}`,
      label: `▤ ${proj}/${type} ${current}/${max} ${filled}`,
      detail: null,
    };
  });
}

function bar(usage: number): string {
  return "▰▰▰▰▰▰▰▰▰▰".slice(0, Math.round(usage * 10)).padEnd(10, "▱");
}

function auditRows(audit: unknown): OverlayRow[] {
  const error = (audit as { error?: string } | undefined)?.error;
  if (error) return [{ key: "audit:err", label: `‼ audit: ${error}`, detail: null }];
  const rows = (audit as { rows?: AnyRecord[] } | undefined)?.rows ?? [];
  if (!Array.isArray(rows)) return [];
  return rows.slice(0, 15).map((row) => {
    const ts = localTimeHHMMSS(String(row.created_at ?? row.ts ?? ""));
    const action = String(row.action ?? row.event ?? "?");
    const actor = String(row.actor ?? row.user_id ?? "");
    return {
      key: `audit:${ts}:${action}`,
      label: `› ${ts} ${actor} ${action}`,
      detail: typeof row.detail === "string" ? row.detail : null,
    };
  });
}

/** Governance overlay rows: ownership entries + quota gauges + audit tail. */
function governanceRows(): OverlayRow[] {
  const gov = workspace.governance;
  if (!gov) {
    return [{ key: "_empty", label: "governance unavailable (bridge down or offline)", detail: null }];
  }
  const rows = [
    ...ownershipRows(gov.ownership),
    ...quotaRows(gov.quotas),
    ...auditRows(gov.audit),
  ];
  return rows.length
    ? rows
    : [{ key: "_empty", label: "no governance data yet (empty ownership/quotas/audit)", detail: null }];
}

export function ListOverlay() {
  const kind = () => workspace.listOverlay as OverlayKind | null;
  const [cursor, setCursor] = createSignal(0);
  const [busy, setBusy] = createSignal(false);
  /** Expanded row (side-by-side diff) for the changes drawer. */
  const [expandedIdx, setExpandedIdx] = createSignal<number | null>(null);
  /** Armed revert (path) — r arms, R fires (two-step destructive confirm). */
  const [revertArmPath, setRevertArmPath] = createSignal<string | null>(null);
  /** W3: job attempt timeline rows loaded from bridge get_job. */
  const [jobGraph, setJobGraph] = createSignal<JobGraphState>({
    loading: false,
    error: null,
    jobId: null,
    state: "",
    attempts: [],
  });
  /** Active-source rows for the /source overlay. */
  const [sourceState, setSourceState] = createSignal<SourceState>({
    loading: false,
    error: null,
    rows: [],
  });
  /** Live per-source health — keyless probes only (G8: gateway /ready,
   *  Ollama /api/tags, keyless /models; a keyed public source is "unknown"). */
  const [sourceHealth, setSourceHealth] = createSignal<Record<string, SourceHealth>>({});
  /** Auth identity line for the connect overlay. */
  const [authIdentity, setAuthIdentity] = createSignal<string | null>(null);

  const fetchAuthIdentity = async () => {
    try {
      const { authStatus } = await import("../lib/connect");
      const status = await authStatus();
      setAuthIdentity(
        status.ok
          ? (status.identity ?? status.raw.split("\n")[0]?.slice(0, 60) ?? "signed in")
          : "not signed in",
      );
    } catch {
      setAuthIdentity("auth unavailable");
    }
  };

  const fetchSourceRows = async () => {
    setSourceState({ loading: true, error: null, rows: [] });
    try {
      const { fetchSources } = await import("../lib/sources");
      const rows = await fetchSources();
      setSourceState({ loading: false, error: null, rows });
      // Probes — parallel, best-effort per source, never with a key (G8).
      const { probeSources } = await import("../lib/source-health");
      const health = await probeSources(
        rows.map((row) => ({
          name: row.name,
          kind: row.kind,
          url: row.url,
          api_key: row.has_api_key,
          provider: row.provider,
        })),
      );
      setSourceHealth(Object.fromEntries(health.map((item) => [item.source, item])));
    } catch (err) {
      setSourceState({
        loading: false,
        error: err instanceof Error ? err.message : String(err),
        rows: [],
      });
    }
  };

  const fetchJobGraph = async (jobId: string): Promise<void> => {
    setJobGraph({ loading: true, error: null, jobId, state: "", attempts: [] });
    try {
      const res = (await getCompanionBridge().request(
        "get_job",
        { job_id: jobId },
        20_000,
      )) as Record<string, unknown>;
      if (workspace.overlayTargetId !== jobId) return; // a newer /job <id> owns the overlay
      const job = (res.job ?? res) as Record<string, unknown>;
      const attempts = Array.isArray(job.attempts) ? job.attempts : [];
      setJobGraph({
        loading: false,
        error: null,
        jobId,
        state: String(job.state ?? job.status ?? "?"),
        attempts: attempts as Array<Record<string, unknown>>,
      });
    } catch (err) {
      if (workspace.overlayTargetId !== jobId) return;
      setJobGraph({
        loading: false,
        error: err instanceof Error ? err.message : String(err),
        jobId,
        state: "",
        attempts: [],
      });
    }
  };
  const dims = useTerminalDims();
  /** Overlay is 70% of the terminal — row caps + diff width track it live
   * (were fixed 76 / 0.68×, clipping rows on wide terminals). */
  const overlayCap = () => Math.max(76, Math.floor((dims().width || 120) * 0.7) - 6);

  /** Fire the armed revert against the bridge and refresh. */
  const executeRevert = async () => {
    const path = revertArmPath();
    if (!path) return;
    setBusy(true);
    setRevertArmPath(null);
    try {
      const res = await getCompanionBridge().request("revert_session_change", { path }, 20_000);
      actions.sendSystemNotice(revertNoticeText(path, res));
      await fetchSessionChanges();
    } catch (err) {
      actions.sendSystemNotice(
        `revert failed: ${err instanceof Error ? err.message : String(err)}`,
      );
    } finally {
      setBusy(false);
    }
  };

  onMount(() => {
    if (kind() === "mcp") void ensureMcpInventory();
    if (kind() === "skills") void ensureSkillsInventory();
    if (kind() === "workflow") void umrsWorkflowActions.loadCatalog();
    if (kind() === "changes") void fetchSessionChanges();
    if (kind() === "governance") void fetchGovernanceOverview();
    if (kind() === "sources") void fetchSourceRows();
    if (kind() === "connect") void fetchAuthIdentity();
  });

  // /job <id>: follow the target id, not its value at mount — the store sets
  // the overlay kind before the id, so onMount read the PREVIOUS id (null on
  // the first open; slash-b#2, 2026-10-21).
  createEffect(
    on(
      () => [kind(), workspace.overlayTargetId] as const,
      ([k, id]) => {
        if (k === "jobgraph" && id) void fetchJobGraph(id);
      },
    ),
  );

  const title = createMemo(() => {
    // Count the RENDERED rows (bridge-authoritative when present), not
    // workspace.skills.length — the raw store mixes three inventories.
    if (kind() === "skills") return `skills (${visibleSkillRows(mergeSkillInventories(workspace.skills)).length})`;
    if (kind() === "mcp") return `mcp servers (${workspace.mcpServers?.length ?? "—"})`;
    if (kind() === "changes") return `session changes (${activeTab().sessionChanges.length} file${activeTab().sessionChanges.length === 1 ? "" : "s"})`;
    if (kind() === "governance") return "governance — ownership / quotas / audit";
    if (kind() === "jobgraph") {
      const graph = jobGraph();
      return `job ${workspace.overlayTargetId ?? "?"} — attempt timeline ${graph.loading ? "(loading)" : `${graph.attempts.length} attempts`}`;
    }
    if (kind() === "sources") return "sources — active gateway switch";
    if (kind() === "connect") return `connect — ${authIdentity() ?? "…"}`;
    return "umrs workflows";
  });

  const rows = createMemo<OverlayRow[]>(() => {
    let out: OverlayRow[];
    if (kind() === "skills") {
      out = visibleSkillRows(mergeSkillInventories(workspace.skills)).map((skill) => {
        // Toggle chip only when the bridge told us the state (● active / ○
        // inactive); a middot for rows merged from CLI-only inventory
        // (state unknown).
        const unknown = skill.active === undefined;
        const tools = skill.tools && skill.tools.length > 0 ? ` · ${skill.tools.length} tools` : "";
        return {
          key: skill.name,
          label: `${unknown ? "· " : ""}${skill.name} · ${skill.source}${skill.hasMcp ? " · +mcp" : ""}${tools}`,
          detail: skill.description ?? null,
          ...(unknown ? {} : { toggle: skill.active === true }),
        };
      });
      return out;
    }
    if (kind() === "changes") {
      out = activeTab().sessionChanges.map((change) => {
        const ins = change.inserted ?? 0;
        const del = change.deleted ?? 0;
        const ed = change.edits ?? 1;
        return {
          key: change.path,
          label: `~ ${change.path}  +${ins}/−${del} · ${ed} edit${ed === 1 ? "" : "s"}`,
          detail: `${change.before.split("\n").length} → ${change.after.split("\n").length} lines · enter = side-by-side diff`,
        };
      });
      return out;
    }
    if (kind() === "governance") {
      return governanceRows();
    }
    if (kind() === "sources") {
      const state = sourceState();
      if (state.loading) return [{ key: "load", label: "loading…", detail: null }];
      if (state.error) return [{ key: "err", label: state.error, detail: null }];
      const healthMap = sourceHealth();
      return state.rows.map((row) => ({
        key: row.name,
        label: sourceRowLabel(row, healthMap[row.name]),
        detail: row.description ?? row.url,
      }));
    }
    if (kind() === "connect") {
      return [
        {
          key: "connect:login",
          label: "🔑 Login with JWT / onboarding token — guided",
          detail: "Enter asks for the token once (masked) and runs /login",
        },
        {
          key: "connect:ollama",
          label: "🦙 Add local Ollama (one-shot) → r4 source add-ollama",
          detail: "Public OpenAI-compatible local endpoint on :11434",
        },
        {
          key: "connect:custom",
          label: "➕ Add public endpoint — guided",
          detail: "Enter asks name → url → key (optional) and registers it",
        },
        {
          key: "connect:logout",
          label: "🚪 Log out of the active profile (r4 auth logout)",
          detail: "Clears credentials only — sources stay registered",
        },
        {
          key: "connect:refresh",
          label: "↻ Refresh connection status",
          detail: "Re-fetch auth identity for the overlay title",
        },
      ];
    }
    if (kind() === "jobgraph") {
      const graph = jobGraph();
      if (graph.loading) return [{ key: "load", label: "loading…", detail: null }];
      if (graph.error) return [{ key: "err", label: graph.error, detail: null }];
      if (graph.attempts.length === 0) {
        return [{ key: "empty", label: `job ${graph.state || "unknown"} · no attempts recorded`, detail: null }];
      }
      return graph.attempts.map((attempt, i) => {
        const state = String(attempt.state ?? attempt.status ?? "?");
        const started = localTimeHHMMSS(String(attempt.started_at ?? attempt.started ?? ""));
        const ended = localTimeHHMMSS(String(attempt.ended_at ?? attempt.ended ?? ""));
        const glyph = statusGlyph(state);
        return {
          key: `attempt:${i}`,
          label: `${glyph} attempt ${i + 1} · ${state} · ${started}${ended ? ` → ${ended}` : ""}`,
          detail: typeof attempt.error === "string" ? attempt.error : typeof attempt.detail === "string" ? attempt.detail : null,
        };
      });
    }
    if (kind() === "mcp") {
      out = (workspace.mcpServers ?? []).map((server) => {
        const isLocal = String(server.source ?? "").startsWith("user-config");
        const src = server.source ? ` · ${server.source}` : "";
        return {
          key: mcpRowKey(server),
          label: `${server.name ?? server.server_id ?? "(unnamed)"} · ${server.status ?? "unknown"}${src}`,
          detail: mcpExplanation(server),
          ...(isLocal ? { toggle: server.enabled !== false } : {}),
        };
      });
      return out;
    }
    // workflow: executions the session knows about + catalog for triggering.
    const execs: OverlayRow[] = Object.values(umrsWorkflowStore.activeExecutions).map(
      (exec) => ({
        key: `exec:${exec.execution_id}`,
        label: `${statusGlyph(exec.status)} ${exec.execution_id.slice(0, 12)} · ${runLabelWithDigest(exec)} · ${exec.status}`,
        detail: executionRowDetail(exec),
        status: exec.status,
      }),
    );
    const catalog: OverlayRow[] = umrsWorkflowStore.catalog.map((wf) => ({
      key: `wf:${wf.workflow_name ?? wf.name ?? "?"}`,
      label: `▸ trigger: ${catalogRowLabel(wf.workflow_name ?? wf.name ?? "?", wf)}`,
      detail: wf.description ?? null,
      status: "available",
    }));
    return [...execs, ...catalog];
  });

  const close = () => actions.closeListOverlay();

  /** /connect → Log out: r4's own outcome line, then a bridge reconnect so no
   *  request carries the old token (connect#1, 2026-10-21: the notice said
   *  "logged out" even for r4's "Already logged out", and the running bridge
   *  kept sending the logged-out Bearer). */
  const logoutAndReconnect = async () => {
    const { logoutNoticeText, runCli } = await import("../lib/connect");
    const res = await runCli(["auth", "logout"]);
    actions.sendSystemNotice(logoutNoticeText(res));
    if (res.ok) {
      // The reconnected bridge reports the real identity; until then the
      // chip must not keep naming the logged-out user.
      actions.setIdentityState({ username: "", credential: "none" });
      try {
        await getCompanionBridge().reconnect();
      } catch (err) {
        actions.sendSystemNotice(
          `bridge reconnect failed (restart the TUI): ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }
    void fetchAuthIdentity();
  };

  /** /connect → Add local Ollama: r4's own line plus the same live probe
   *  `/source add` prints — a dead endpoint made active said only "added"
   *  (connect#9). */
  const addOllamaWithProbe = async () => {
    const { addOllama, firstOutputLine } = await import("../lib/connect");
    const res = await addOllama();
    if (!res.ok) {
      actions.sendSystemNotice(`ollama add failed: ${res.output}`);
      return;
    }
    const { probeNewSource } = await import("../commands/source-command");
    const probe = await probeNewSource("ollama");
    const dead = probe.startsWith("probe:") ? " — is `ollama serve` running?" : "";
    actions.sendSystemNotice(`${firstOutputLine(res.output) || "ollama source added"} · ${probe}${dead}`);
  };

  const runConnectRow = async (key: string) => {
    if (key === "connect:login" || key === "connect:custom") {
      // The Q&A wizard asks for each value one prompt at a time and
      // dispatches the built line; openConnectWizard closes this overlay.
      actions.openConnectWizard(key === "connect:login" ? "login" : "source");
      return;
    }
    if (key === "connect:ollama") return addOllamaWithProbe();
    if (key === "connect:logout") return logoutAndReconnect();
    if (key === "connect:refresh") void fetchAuthIdentity();
  };

  const primaryAction = async (index: number) => {
    const row = rows()[index];
    if (!row) return;
    // Changes drawer: Enter toggles the SIDE-BY-SIDE diff view (session diff).
    if (kind() === "changes") {
      setExpandedIdx(expandedIdx() === index ? null : index);
      return;
    }
    // Sources overlay: Enter switches the active gateway source.
    if (kind() === "sources") {
      setBusy(true);
      try {
        const { useSource } = await import("../lib/sources");
        const result = await useSource(row.key);
        if (!result.ok) {
          actions.sendSystemNotice(`source switch failed: ${result.stderr}`);
          return;
        }
        actions.sendSystemNotice(`source → ${row.key} · reconnecting companion…`);
        try {
          await getCompanionBridge().reconnect();
        } catch (err) {
          actions.sendSystemNotice(
            `bridge reconnect failed (restart the TUI): ${err instanceof Error ? err.message : String(err)}`,
          );
        }
        await fetchSourceRows();
      } finally {
        setBusy(false);
      }
      return;
    }
    if (kind() === "connect") {
      setBusy(true);
      try {
        await runConnectRow(row.key);
      } finally {
        setBusy(false);
      }
      return;
    }
    if (kind() === "skills") {
      const skill = workspace.skills.find((s) => s.name === row.key);
      if (!skill) return;
      // Bridge-backed rows: Enter TOGGLES activation (opencode-style select).
      // State unknown (CLI-only list): fall back to the detail notice.
      if (skill.active !== undefined && !isOfflineBackend()) {
        setBusy(true);
        try {
          await toggleSkillActivation(skill.name, !skill.active);
        } finally {
          setBusy(false);
        }
        return;
      }
      actions.sendSystemNotice(skillNoticeText(skill));
      return;
    }
    if (kind() === "mcp") {
      const server = (workspace.mcpServers ?? []).find((s) => mcpRowKey(s) === row.key);
      if (!server) return;
      // Local (user-config) rows: Enter toggles on/off in the config file.
      // Registry rows stay read-only here (governed by `r4 mcp unregister`).
      if (String(server.source ?? "").startsWith("user-config")) {
        setBusy(true);
        try {
          await toggleMcpServerEnabled(server);
        } finally {
          setBusy(false);
        }
        return;
      }
      actions.sendSystemNotice(mcpServerNoticeText(server));
      return;
    }
    // workflow Enter: show execution detail / describe target.
    if (row.key.startsWith("exec:")) {
      const execId = row.key.slice(5);
      actions.sendSystemNotice(
        `workflow execution ${execId} — [s] status · [r] results · [c] cancel · [a] approve-gate`,
      );
    } else {
      const name = row.key.slice(3);
      actions.sendSystemNotice(`trigger ${name}: press [t] in the overlay · (/workflow run ${name} in chat with params)`);
    }
  };

  const workflowKeyAction = async (key: string, index: number) => {
    if (kind() !== "workflow") return false;
    const row = rows()[index];
    if (!row || busy()) return false;
    setBusy(true);
    try {
      if (key === "s" && row.key.startsWith("exec:")) {
        const exec = await umrsWorkflowActions.refreshStatus(row.key.slice(5));
        actions.sendSystemNotice(
          `status ${exec.execution_id.slice(0, 12)}: ${exec.status}${progressSuffix(exec.progress, " ")}`,
        );
        return true;
      }
      if (key === "r" && row.key.startsWith("exec:")) {
        const exec = await umrsWorkflowActions.fetchResults(row.key.slice(5));
        const text = exec.error ?? (exec.ok === false ? "results unavailable" : "results ready — see /workflow results " + exec.execution_id.slice(0, 12));
        actions.sendSystemNotice(`results ${exec.execution_id.slice(0, 12)}: ${text}`);
        return true;
      }
      if (key === "c" && row.key.startsWith("exec:")) {
        const exec = await umrsWorkflowActions.cancel(row.key.slice(5));
        actions.sendSystemNotice(`cancel ${exec.execution_id.slice(0, 12)} → ${exec.status}`);
        return true;
      }
      if (key === "a" && row.key.startsWith("exec:")) {
        // Manual-approval gate (W5): discover the waiting step from the live
        // detail, then submit the decision — the only path to this verb in
        // the console (workflow.approve_gate was bridge-served but unwired).
        const execId = row.key.slice(5);
        const detail = await umrsWorkflowActions.fetchDetail(execId);
        const stepName = waitingApprovalStepName(detail);
        if (!stepName) {
          actions.sendSystemNotice(
            `approve ${execId.slice(0, 12)}: no gate waiting for approval (${detail.status})`,
          );
          return true;
        }
        const exec = await umrsWorkflowActions.approveGate(execId, stepName);
        actions.sendSystemNotice(
          `approved gate ${stepName} on ${execId.slice(0, 12)} → ${exec.status}`,
        );
        return true;
      }
      if (key === "t" && row.key.startsWith("wf:")) {
        const exec = await umrsWorkflowActions.run(row.key.slice(3));
        actions.sendSystemNotice(
          `triggered ${row.key.slice(3)} → ${exec.execution_id.slice(0, 12)} (${exec.status})`,
        );
        return true;
      }
    } catch (err) {
      actions.sendSystemNotice(
        `workflow action failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      return true;
    } finally {
      setBusy(false);
    }
    return false;
  };

  const footerHint = createMemo(() => {
    if (kind() === "workflow")
      return "↑↓ select · Enter detail · s status · r results · c cancel · t trigger · a approve-gate · Esc close";
    if (kind() === "skills") return "↑↓ select · Enter toggle activate/●○ · Esc close";
    if (kind() === "changes") {
      const armed = revertArmPath();
      if (armed) {
        return `⚠ armed: ${armed} · R revert to session before · r cancel · Esc close`;
      }
      return "↑↓ select · Enter side-by-side diff · r arm revert · Esc close";
    }
    if (kind() === "governance" || kind() === "jobgraph") return "↑↓ select · Esc close";
    if (kind() === "sources") return "↑↓ select · Enter switch · p push · u unpush · h recheck health · Esc close";
    if (kind() === "connect") return "↑↓ select · Enter run · Esc close";
    if (kind() === "mcp") return "↑↓ select · Enter toggle ●/○ (local rows) / detail (registry rows) · Esc close";
    return "↑↓ select · Enter detail · Esc close";
  });

  const onKey = (event: KeyEvent) => {
    if (theLetterAction(event)) {
      event.preventDefault();
      return;
    }
    handleListKey(event.name, {
      selected: cursor(),
      length: rows().length,
      ctrl: false,
      onMove: (index) => {
        event.preventDefault();
        setCursor(clampIndex(index, rows().length));
      },
      onEnter: (index) => {
        event.preventDefault();
        void primaryAction(index);
      },
      onEscape: () => {
        event.preventDefault();
        close();
      },
    });
  };

  /** Changes overlay: r arms/disarms, R reverts. OpenTUI reports R as
   * name "r" + shift (never "shift+r"), so R used to disarm instead of
   * reverting (TUI audit 2026-10-17). */
  const changesLetterAction = (event: KeyEvent): boolean => {
    const isUpperR = event.name === "R" || (event.name === "r" && Boolean(event.shift));
    if (isUpperR || event.name === "shift+r") {
      void executeRevert();
      return true;
    }
    if (event.name !== "r") return false;
    const row = rows()[cursor()];
    if (row) setRevertArmPath(revertArmPath() === row.key ? null : row.key);
    return true;
  };

  /** Letter-driven actions (workflow + changes verbs) must run before list nav. */
  const theLetterAction = (event: KeyEvent): boolean => {
    if (kind() === "workflow") {
      if (
        event.name === "s" ||
        event.name === "r" ||
        event.name === "c" ||
        event.name === "t" ||
        event.name === "a"
      ) {
        void workflowKeyAction(event.name, cursor());
        return true;
      }
      return false;
    }
    if (kind() === "changes") return changesLetterAction(event);
    if (kind() === "sources") {
      if (event.name === "h") {
        void fetchSourceRows();
        return true;
      }
      // Wave 2026-09-21 (G-RS-21): push / unpush the selected source through
      // the bridge (sealed server-side; the key never crosses the TUI).
      if (event.name === "p" || event.name === "u") {
        const row = rows()[cursor()];
        if (!row || busy()) return true;
        setBusy(true);
        void import("../bridge/venue-bridge")
          .then(async (m) => {
            if (event.name === "p") await m.pushSource(row.key);
            else await m.unpushSource(row.key);
          })
          .then(() => fetchSourceRows())
          .finally(() => setBusy(false));
        return true;
      }
      return false;
    }
    return false;
  };

  const empty = createMemo(() => rows().length === 0);
  const emptyText = createMemo(() => {
    if (kind() === "skills") return "No skills reported by the backend.";
    if (kind() === "changes") return "No file edits in this session yet.";
    if (kind() === "governance")
      return isOfflineBackend()
        ? "offline/demo backend — no governance inventory"
        : "Loading governance data…";
    if (kind() === "mcp") {
      return isOfflineBackend() ? "offline/demo backend — no gateway inventory" : "No MCP servers registered.";
    }
    // Sources had no case and fell through to the workflow catalog's text
    // (connect#7 / slash-b#4, 2026-10-21).
    if (kind() === "sources") {
      return "No sources registered — /connect → Add public endpoint, or /source add <name> --url <url>";
    }
    if (umrsWorkflowStore.lastError) return `workflow catalog: ${umrsWorkflowStore.lastError}`;
    if (isOfflineBackend()) return "offline/demo backend — workflows unavailable";
    return "No workflows yet — trigger with /workflow run <name> or see catalog.";
  });

  return (
    <Floating
      position="absolute"
      width="70%"
      height="70%"
      left="15%"
      top="12%"
      title={title()}
      flexDirection="column"
      padding={1}
    >
      <scrollbox flexGrow={1} scrollY stickyScroll stickyStart="top" overflow="hidden">
        <Show when={!empty()} fallback={<text fg={palette.fgDim}>{emptyText()}</text>}>
          <For each={rows()}>
            {(row, idx) => (
              <>
                <box
                  height={1}
                  width="100%"
                  backgroundColor={idx() === cursor() ? palette.bgHover : undefined}
                  onMouseDown={() => {
                    setCursor(idx());
                    if (kind() === "changes" && idx() === expandedIdx()) setExpandedIdx(null);
                  }}
                >
                  {/* opentui 0.3.4 reconciler defect: a REACTIVE StyledText
                      (t`…` reading a signal) appends its chunks to the text
                      buffer on every re-render — rows duplicated once per
                      arrow key (2026-10-01). Reactive pieces stay PLAIN
                      STRINGS (the string path replaces); only the per-row
                      STATIC ●/○ chip is styled, via the fg PROP. */}
                  <box flexDirection="row" height={1} width="100%">
                    <text
                      fg={idx() === cursor() ? palette.fgBright : palette.fg}
                      onMouseDown={() => {
                        setCursor(idx());
                        if (kind() === "changes") {
                          setExpandedIdx(expandedIdx() === idx() ? null : idx());
                        }
                      }}
                      children={`${idx() === cursor() ? "▸" : " "} `}
                    />
                    <Show when={row.toggle !== undefined}>
                      <text
                        fg={row.toggle ? palette.statusOk : palette.fgDim}
                        onMouseDown={() => {
                          setCursor(idx());
                          if (kind() === "changes") {
                            setExpandedIdx(expandedIdx() === idx() ? null : idx());
                          }
                        }}
                        children={row.toggle ? "● " : "○ "}
                      />
                    </Show>
                    <text
                      fg={idx() === cursor() ? palette.fgBright : palette.fg}
                      onMouseDown={() => {
                        setCursor(idx());
                        if (kind() === "changes") {
                          setExpandedIdx(expandedIdx() === idx() ? null : idx());
                        }
                      }}
                      children={truncate(row.label, overlayCap() - (row.toggle === undefined ? 2 : 4))}
                    />
                  </box>
                </box>
                <Show when={kind() === "changes" && expandedIdx() === idx()}>
                  <box width="100%" paddingLeft={1}>
                    <SideBySideDiff
                      diffText={
                        activeTab().sessionChanges.find((change) => change.path === row.key)
                          ?.diff_text ?? ""
                      }
                      width={overlayCap() - 6}
                      maxRows={28}
                    />
                  </box>
                </Show>
              </>
            )}
          </For>
        </Show>
      </scrollbox>
      <Show when={rows()[cursor()]?.detail}>
        <box height={1} width="100%" paddingLeft={1} overflow="hidden" marginTop={1}>
          <text fg={palette.fgDim}>{truncate(rows()[cursor()]?.detail ?? "", overlayCap() - 2)}</text>
        </box>
      </Show>
      <box height={1} width="100%" marginTop={1} overflow="hidden">
        <text fg={busy() ? palette.statusWarn : palette.fgDim}>
          {busy() ? "working…" : footerHint()}
        </text>
      </box>
      <box height={1} width={1} overflow="hidden">
        <KeyTrap focused onKeyDown={onKey} />
      </box>
    </Floating>
  );
}
