/**
 * Model tree view — sidebar panel for browsing the UMRS model
 * registry via Gateway /gw/models.
 */

import * as vscode from "vscode";
import { GatewayClient, type Model } from "../gateway-client";
import type { AuthManager } from "../auth";

/** Format a context length for display (e.g. 131072 → "128K"). */
function formatCtx(n: number): string {
  if (n >= 1024 && n % 1024 === 0) {
    return `${n / 1024}K`;
  }
  if (n >= 1000) {
    return `${Math.round(n / 1024)}K`;
  }
  return String(n);
}

export class ModelTreeProvider
  implements vscode.TreeDataProvider<ModelItem>, vscode.Disposable
{
  private readonly _onDidChange = new vscode.EventEmitter<ModelItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  /** Last-seen model count keyed by source name. Used to detect catalog
   *  changes (new registration, deletion, publish) between refresh cycles
   *  so the tree and LM provider stay in sync without extra polling. */
  private lastCounts: Map<string, number> = new Map();

  constructor(
    private readonly getClient: () => GatewayClient | undefined,
    private readonly auth?: AuthManager,
  ) {}

  getTreeItem(element: ModelItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<ModelItem[]> {
    const client = this.getClient();
    if (!client) {
      return [
        new ModelItem("Not connected", "", "model-offline", vscode.TreeItemCollapsibleState.None),
      ];
    }

    // Multi-source aggregation: when more than one LLM source is configured,
    // poll each source and tag every model with the source it actually came
    // from. This prevents rccs-only models from being mislabeled as belonging
    // to the active (e.g. ai4s) source, and surfaces public OpenAI-compatible
    // sources alongside platform ones.
    const sourceConfigs = this.auth?.listLlmSourceConfigs() ?? [];

    let models: Model[];
    if (sourceConfigs.length > 1) {
      const results = await Promise.allSettled(
        sourceConfigs.map(async (cfg) => {
          const srcClient = new GatewayClient({
            baseUrl: cfg.url,
            token: cfg.token,
            kind: cfg.kind,
          });
          const found = await srcClient.listModels();
          return found.map((m) => ({ ...m, _source: cfg.name }));
        }),
      );
      models = [];
      const errors: string[] = [];
      for (let i = 0; i < results.length; i++) {
        const r = results[i];
        if (r.status === "fulfilled") {
          models.push(...r.value);
        } else {
          const reason = r.reason instanceof Error ? r.reason.message : String(r.reason);
          errors.push(`${sourceConfigs[i].name}: ${reason}`);
        }
      }
      // Only surface an error item when every source failed.
      if (models.length === 0 && errors.length > 0) {
        return [
          new ModelItem(
            "Error loading models",
            errors.join("; "),
            "model-error",
            vscode.TreeItemCollapsibleState.None,
          ),
        ];
      }
    } else {
      try {
        models = await client.listModels();
      } catch (err: unknown) {
        const detail = err instanceof Error ? err.message : "Check Gateway connection";
        return [
          new ModelItem("Error loading models", detail, "model-error", vscode.TreeItemCollapsibleState.None),
        ];
      }
    }

    if (!models || models.length === 0) {
      return [
        new ModelItem("No models registered", "", "model-empty", vscode.TreeItemCollapsibleState.None),
      ];
    }

    // ── Detect model catalog changes (new registrations, deletions) ──
    // When model count changes for any source, fire an extra refresh so the
    // LM provider and all tree views see the latest catalog without waiting
    // for the next polling cycle.
    const counts = new Map<string, number>();
    for (const m of models) {
      const src = m._source ?? "__default__";
      counts.set(src, (counts.get(src) ?? 0) + 1);
    }
    let changed = false;
    for (const [src, count] of counts) {
      if (this.lastCounts.get(src) !== count) {
        changed = true;
        this.lastCounts.set(src, count);
      }
    }
    // Remove sources that disappeared (all models deleted for that source)
    for (const src of this.lastCounts.keys()) {
      if (!counts.has(src)) {
        changed = true;
        this.lastCounts.delete(src);
      }
    }
    if (changed) {
      // Fire a follow-up refresh so VS Code re-queries the tree and LM
      // provider gets the updated capabilities.
      setTimeout(() => this._onDidChange.fire(undefined), 100);
    }

    // Resolve the active source name as a fallback label for the
    // single-source path (no per-model _source available).
    let sourceName: string | undefined;
    if (this.auth) {
      try {
        sourceName = await this.auth.getActiveSourceName();
      } catch {
        // Degrade gracefully
      }
    }

    // Stable ordering: group by source, then model name.
    models.sort((a, b) => {
      const sa = a._source ?? sourceName ?? "";
      const sb = b._source ?? sourceName ?? "";
      if (sa !== sb) {
        return sa.localeCompare(sb);
      }
      return a.name.localeCompare(b.name);
    });

    return models.map((m) => {
      const scopeIcon =
        m.scope === "system"
          ? new vscode.ThemeIcon("server")
          : m.scope === "project"
            ? new vscode.ThemeIcon("organization")
            : new vscode.ThemeIcon("person");

      // Description: scope · model_type · ctx · via <true source>
      const descParts = [m.scope];
      if (m.model_type) {
        descParts.push(m.model_type);
      }
      if (m.max_model_len) {
        descParts.push(`ctx ${formatCtx(m.max_model_len)}`);
      }
      const sourceLabel = m._source ?? sourceName;
      if (sourceLabel) {
        descParts.push(`via ${sourceLabel}`);
      }

      const item = new ModelItem(
        m.name ?? "",
        descParts.join(" · "),
        `model-${m.scope}`,
        vscode.TreeItemCollapsibleState.None,
      );
      item.iconPath = scopeIcon;
      item.modelId = m.id;
      item.modelName = m.name;
      item.modelScope = m.scope;
      item.modelSource = m._source;
      return item;
    });
  }

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}

export class ModelItem extends vscode.TreeItem {
  modelId?: string;
  modelName?: string;
  modelScope?: string;
  modelSource?: string;

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
