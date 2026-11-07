/**
 * Job tree view — sidebar panel for listing and managing
 * durable jobs via Gateway /gw/jobs/*.
 */

import * as vscode from "vscode";
import { GatewayClient, type Job } from "../gateway-client";

const STATUS_ICONS: Record<string, vscode.ThemeIcon> = {
  pending: new vscode.ThemeIcon("clock"),
  running: new vscode.ThemeIcon("loading~spin"),
  completed: new vscode.ThemeIcon("pass-filled", new vscode.ThemeColor("testing.iconPassed")),
  failed: new vscode.ThemeIcon("error", new vscode.ThemeColor("testing.iconFailed")),
  cancelled: new vscode.ThemeIcon("circle-slash"),
  lost: new vscode.ThemeIcon("warning", new vscode.ThemeColor("problemsWarningIcon.foreground")),
};

export class JobTreeProvider
  implements vscode.TreeDataProvider<JobItem>, vscode.Disposable
{
  private readonly _onDidChange = new vscode.EventEmitter<JobItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;
  private timer: ReturnType<typeof setInterval> | undefined;

  constructor(private readonly getClient: () => GatewayClient | undefined) {}

  // ── TreeDataProvider ───────────────────────────────────────

  getTreeItem(element: JobItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<JobItem[]> {
    const client = this.getClient();
    if (!client) {
      return [
        new JobItem(
          "Not connected",
          "Login to R4 Gateway first",
          "job-offline",
          vscode.TreeItemCollapsibleState.None,
        ),
      ];
    }

    // Public sources are plain OpenAI-compatible endpoints with no durable job
    // control plane. Show an intentional placeholder rather than a 404 error.
    if (client.isPublic()) {
      return [
        new JobItem(
          "Not available for public sources",
          "Jobs require a platform (UMRS gateway) source",
          "job-unavailable",
          vscode.TreeItemCollapsibleState.None,
        ),
      ];
    }

    let jobs: Job[];
    try {
      jobs = await client.listJobs();
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : "Check Gateway connection";
      return [
        new JobItem(
          "Error loading jobs",
          detail,
          "job-error",
          vscode.TreeItemCollapsibleState.None,
        ),
      ];
    }

    if (jobs.length === 0) {
      return [
        new JobItem(
          "No jobs",
          "",
          "job-empty",
          vscode.TreeItemCollapsibleState.None,
        ),
      ];
    }

    return jobs.slice(0, 50).map((j) => {
      const statusLower = j.status?.toLowerCase() ?? "";
      const contextValue = `job-${statusLower}`;
      const icon = STATUS_ICONS[statusLower] ?? new vscode.ThemeIcon("circle-outline");
      const jobId = j.id ?? "";
      const shortId = jobId.length > 8 ? `${jobId.slice(0, 8)}…` : jobId;

      const item = new JobItem(
        `${shortId}  ${j.type ?? ""}`,
        `${j.status ?? ""}${j.created_at ? ` · ${j.created_at}` : ""}`,
        contextValue,
        vscode.TreeItemCollapsibleState.None,
      );
      item.iconPath = icon;
      item.jobId = j.id;
      if (j.error) {
        item.tooltip = `Error: ${j.error}`;
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

export class JobItem extends vscode.TreeItem {
  jobId?: string;

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
