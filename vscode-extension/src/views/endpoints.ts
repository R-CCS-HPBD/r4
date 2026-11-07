/**
 * Endpoint tree view — sidebar panel for listing and managing
 * inference endpoints via Gateway /gw/endpoints/*.
 */

import * as vscode from "vscode";
import { GatewayClient, type Endpoint, type Model } from "../gateway-client";
import type { AuthManager } from "../auth";

/**
 * Map UMRS readiness-probe detail strings to human-readable phase labels.
 *
 * The UMRS endpoint registry updates the `detail` field with probe progress
 * such as "probe #3: starting LiteLLM / waiting for service port". This
 * function shortens these to compact phase labels for tree-view display.
 */
function formatReadinessPhase(detail: string): string {
  const lower = detail.toLowerCase();

  // Extract probe number if present: "probe #N: ..." → "poll #N"
  const probeMatch = lower.match(/probe #(\d+)/);
  const probePrefix = probeMatch ? `poll #${probeMatch[1]}` : "";

  if (lower.includes("waiting for backend allocation") || lower.includes("submitting to backend") || lower.includes("submitted to backend")) {
    return probePrefix ? `${probePrefix}: queued` : "queued";
  }
  if (lower.includes("starting litellm") || lower.includes("waiting for service port") || lower.includes("connection refused") || lower.includes("connection error")) {
    return probePrefix ? `${probePrefix}: starting` : "starting";
  }
  if (lower.includes("loading model") || lower.includes("waiting for inference warmup") || lower.includes("chat probe timeout") || lower.includes("request timeout")) {
    return probePrefix ? `${probePrefix}: loading` : "loading";
  }
  if (lower.includes("registering served model") || lower.includes("no models in response") || lower.includes("expected model")) {
    return probePrefix ? `${probePrefix}: registering` : "registering";
  }
  if (lower.includes("endpoint reachable") || lower.includes("chat probe error") || lower.includes("chat probe http")) {
    return probePrefix ? `${probePrefix}: warming` : "warming";
  }
  if (lower.includes("waiting for endpoint registration")) {
    return probePrefix ? `${probePrefix}: registering` : "registering";
  }
  if (lower.includes("ready")) {
    return "ready";
  }
  // Fallback: truncate long detail strings
  return detail.length > 40 ? detail.slice(0, 37) + "…" : detail;
}

export class EndpointTreeProvider
  implements vscode.TreeDataProvider<EndpointItem>, vscode.Disposable
{
  private readonly _onDidChange = new vscode.EventEmitter<EndpointItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;
  private endpoints: Endpoint[] = [];
  private timer: ReturnType<typeof setInterval> | undefined;

  constructor(
    private readonly getClient: () => GatewayClient | undefined,
    private readonly auth?: AuthManager,
    /** Optional callback invoked before each endpoint list refresh.
     *  Used for reconciliation (clearing stale workspaceState keys). */
    private readonly onBeforeRefresh?: () => Promise<void>,
  ) {}

  // ── TreeDataProvider ───────────────────────────────────────

  getTreeItem(element: EndpointItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<EndpointItem[]> {
    const client = this.getClient();
    if (!client) {
      return [
        new EndpointItem(
          "Not connected",
          "Login to R4 Gateway first",
          "endpoint-offline",
          vscode.TreeItemCollapsibleState.None,
        ),
      ];
    }

    // Retry stale-key reconciliation before each list when gateway was
    // previously unreachable during initClient.
    if (this.onBeforeRefresh) {
      try {
        await this.onBeforeRefresh();
      } catch {
        // Best-effort — reconciliation failures must not block listing
      }
    }

    // Multi-source aggregation: when more than one LLM source is configured,
    // poll each source and tag every endpoint with the source it actually came
    // from. This mirrors the ModelTreeProvider fix (2026-06-10) — without this,
    // only the ACTIVE source's endpoints are ever shown, so endpoints on
    // non-active sources (e.g. bdrt) silently disappear from the tree. Public
    // OpenAI-compatible sources report each advertised model as a ready
    // endpoint.
    const sourceConfigs = this.auth?.listLlmSourceConfigs() ?? [];
    const modelsBySource = new Map<string, Model[]>();

    if (sourceConfigs.length > 1) {
      const results = await Promise.allSettled(
        sourceConfigs.map(async (cfg) => {
          const srcClient = new GatewayClient({
            baseUrl: cfg.url,
            token: cfg.token,
            kind: cfg.kind,
          });
          const found = await srcClient.listEndpoints();
          return { cfg, endpoints: found.map((ep) => ({ ...ep, _source: cfg.name })) };
        }),
      );
      this.endpoints = [];
      const errors: string[] = [];
      for (let i = 0; i < results.length; i++) {
        const r = results[i];
        if (r.status === "fulfilled") {
          this.endpoints.push(...r.value.endpoints);
        } else {
          const reason = r.reason instanceof Error ? r.reason.message : String(r.reason);
          errors.push(`${sourceConfigs[i].name}: ${reason}`);
        }
      }
      if (this.endpoints.length === 0 && errors.length > 0) {
        return [
          new EndpointItem(
            "Error loading endpoints",
            errors.join("; "),
            "endpoint-error",
            vscode.TreeItemCollapsibleState.None,
          ),
        ];
      }
    } else {
      try {
        this.endpoints = await client.listEndpoints();
      } catch (err: unknown) {
        const detail = err instanceof Error ? err.message : "Check Gateway connection";
        return [
          new EndpointItem(
            "Error loading endpoints",
            detail,
            "endpoint-error",
            vscode.TreeItemCollapsibleState.None,
          ),
        ];
      }
    }

    if (!this.endpoints || this.endpoints.length === 0) {
      return [
        new EndpointItem(
          "No active endpoints",
          "Use R4: Provision Endpoint to start one",
          "endpoint-empty",
          vscode.TreeItemCollapsibleState.None,
        ),
      ];
    }

    // Resolve per-source URL and source name for display
    const auth = this.auth;
    let sourceName: string | undefined;
    let sourceUrl: string | undefined;
    let isAdminView = false;
    if (auth) {
      try {
        sourceName = await auth.getActiveSourceName();
        const src = await auth.getActiveCliSource();
        sourceUrl = src?.url ?? src?.gateway_url;
        isAdminView = await auth.isAdmin();
      } catch {
        // Degrade gracefully — keep source display empty
      }
    }

    // Hide terminal-state endpoints (stopped/terminated/failed/expired)
    const terminalStates = new Set(["terminated", "failed", "expired", "stopped"]);
    const activeEndpoints = this.endpoints.filter(
      (ep) => !terminalStates.has(ep.status?.toLowerCase() ?? ""),
    );

    // ── Cross-reference: detect endpoints whose model is missing/deprecated ──
    // Fetch model catalog once per tree refresh. Build a set of known model
    // names so we can flag endpoints that reference deleted or unavailable models.
    // In the multi-source case, fetch per-source so an endpoint is only
    // flagged "missing" against its OWN source's catalog, not a different one.
    let modelNames: Set<string> = new Set();
    let deprecatedModels: Set<string> = new Set();
    try {
      // Only fetch if there are active endpoints (avoid unnecessary HTTP call)
      if (activeEndpoints.length > 0) {
        if (sourceConfigs.length > 1) {
          const results = await Promise.allSettled(
            sourceConfigs.map(async (cfg) => {
              const srcClient = new GatewayClient({ baseUrl: cfg.url, token: cfg.token });
              return { name: cfg.name, models: await srcClient.listModels() };
            }),
          );
          for (const r of results) {
            if (r.status === "fulfilled") {
              modelsBySource.set(r.value.name, r.value.models);
            }
          }
        } else {
          const models = await client.listModels();
          for (const m of models) {
            const id = m.id ?? m.model_id ?? m.name;
            modelNames.add(m.name);
            modelNames.add(id);
            if (m.status?.toLowerCase() === "deprecated") {
              deprecatedModels.add(m.name);
              deprecatedModels.add(id);
            }
          }
        }
      }
    } catch {
      // Can't cross-reference — proceed without model status info
    }

    return activeEndpoints.map((ep) => {
      const statusLower = ep.status?.toLowerCase() ?? "";
      const ownerType = ep.owner_type ?? "user";
      const contextValue = `endpoint-${statusLower}-${ownerType}`;

      // Detect stale provisioning: provisioning/starting >10 min with no
      // progress update. Surface a warning icon so the user knows something
      // may be stuck.
      const provisioningStates = new Set(["provisioning", "pending", "starting"]);
      const staleProvision =
        provisioningStates.has(statusLower) &&
        ep.created_at &&
        Date.now() - new Date(ep.created_at).getTime() > 10 * 60_000;

      // Detect degraded endpoints: status="ready" but the readiness detail
      // or last-used timestamp suggests the endpoint is unreachable or stale.
      const degradedReady =
        statusLower === "ready" &&
        ep.last_used_at &&
        Date.now() - new Date(ep.last_used_at).getTime() > 30 * 60_000 &&
        ep.detail &&
        ep.detail.toLowerCase().includes("unreachable");

      // Detect model-level issues: model deleted from catalog or deprecated.
      // Resolve per-endpoint model sets when multi-source (own catalog only).
      let epModelNames = modelNames;
      let epDeprecatedModels = deprecatedModels;
      if (ep._source && modelsBySource.has(ep._source)) {
        epModelNames = new Set();
        epDeprecatedModels = new Set();
        for (const m of modelsBySource.get(ep._source) ?? []) {
          const id = m.id ?? m.model_id ?? m.name;
          epModelNames.add(m.name);
          epModelNames.add(id);
          if (m.status?.toLowerCase() === "deprecated") {
            epDeprecatedModels.add(m.name);
            epDeprecatedModels.add(id);
          }
        }
      }
      const epModel = ep.model ?? "";
      const modelMissing = epModelNames.size > 0 && epModel && !epModelNames.has(epModel);
      const modelDeprecated = epDeprecatedModels.has(epModel);

      const hasIssue = staleProvision || degradedReady || modelMissing || modelDeprecated;
      const icon = statusLower === "ready" && !hasIssue
        ? new vscode.ThemeIcon("pass-filled", new vscode.ThemeColor("testing.iconPassed"))
        : hasIssue
          ? new vscode.ThemeIcon("warning", new vscode.ThemeColor("notificationsWarningIcon.foreground"))
          : statusLower === "provisioning" || statusLower === "starting"
            ? new vscode.ThemeIcon("loading~spin")
            : new vscode.ThemeIcon("circle-outline");

      // Build a warning tooltip for cross-reference issues
      const warnings: string[] = [];
      if (modelMissing) {
        warnings.push(`Model "${epModel}" not found in catalog — may have been deleted`);
      }
      if (modelDeprecated) {
        warnings.push(`Model "${epModel}" is deprecated — consider re-provisioning`);
      }
      if (staleProvision) {
        warnings.push("Provisioning has been stuck for >10 minutes");
      }

      // Description: show where the endpoint actually runs.
      // 1. Endpoint host (actual compute node, e.g. gpu001) — most useful,
      //    UNLESS it's 127.0.0.1 (SSH tunnel — meaningless to display).
      // 2. Source/profile name (e.g. "rccs-cloud") — meaningful & stable
      // 3. Gateway URL hostname — fallback only
      // Gateway URL hostname is meaningless when connecting via SSH tunnel
      // (shows 127.0.0.1), so always prefer ep.host (when not loopback) or
      // source name.
      let sourceDisplay = "";
      const isLoopbackHost =
        ep.host === "127.0.0.1" || ep.host === "localhost" || ep.host === "::1";
      if (ep.host && !isLoopbackHost) {
        sourceDisplay = ep.host;
      } else if (ep._source) {
        sourceDisplay = ep._source;
      } else if (sourceName) {
        sourceDisplay = sourceName;
      } else if (sourceUrl) {
        try {
          const hostname = new URL(sourceUrl).hostname;
          sourceDisplay = hostname.split(".")[0] ?? hostname;
        } catch {
          sourceDisplay = sourceUrl;
        }
      }
      const descParts: string[] = [];
      if (sourceDisplay) {
        descParts.push(sourceDisplay);
      }
      // Surface the provisioning reason/detail (e.g. "queued: MaxMemPerLimit")
      // so a stuck or pending provision is no longer invisible.
      const reason = ep.detail ?? ep.error_message;
      if (reason && statusLower !== "ready") {
        descParts.push(formatReadinessPhase(reason));
      }

      const item = new EndpointItem(
        ep.variant
          ? `${ep.model ?? ""}  [${ep.variant}]`
          : (ep.model ?? ""),
        descParts.join("  "),
        contextValue,
        vscode.TreeItemCollapsibleState.None,
      );
      item.iconPath = icon;
      item.endpointId = ep.job_id;
      item.modelName = ep.model;
      item.endpointSource = ep._source;
      item.ownerType = ownerType;
      item.isAdminView = isAdminView;

      // Show litellm_url or cross-reference warnings as tooltip
      const tooltipParts: string[] = [];
      if (ep.litellm_url) {
        tooltipParts.push(`LiteLLM: ${ep.litellm_url}`);
      }
      tooltipParts.push(...warnings);
      if (reason && tooltipParts.length === 0) {
        tooltipParts.push(reason);
      }
      if (tooltipParts.length > 0) {
        item.tooltip = tooltipParts.join("\n");
      }

      if (statusLower === "ready") {
        item.command = {
          command: "r4.useEndpoint",
          title: "Use this endpoint",
          arguments: [item],
        };
      }
      return item;
    });
  }

  // ── Refresh / polling ──────────────────────────────────────

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  startPolling(intervalMs: number): void {
    this.stopPolling();
    if (intervalMs > 0) {
      this.timer = setInterval(() => this.refresh(), intervalMs);
    }
  }

  stopPolling(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
    }
  }

  dispose(): void {
    this.stopPolling();
    this._onDidChange.dispose();
  }
}

export class EndpointItem extends vscode.TreeItem {
  endpointId?: string;
  modelName?: string;
  endpointSource?: string;
  ownerType?: string;
  isAdminView?: boolean;

  constructor(
    label: string,
    description: string,
    contextValue: string,
    collapsibleState: vscode.TreeItemCollapsibleState,
  ) {
    super(label, collapsibleState);
    this.description = description;
    this.contextValue = contextValue;
  }
}
