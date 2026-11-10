/**
 * Workflow tree view — sidebar panel for browsing and triggering
 * workflows via Gateway /gw/workflows/*.
 *
 * ADR-113 WV4 (2026-11-09): a workflow row reads `name` with
 * `v<default> (+N)`; one with several versions expands to a child per
 * version, and triggering a child runs exactly that version.
 */

import * as vscode from "vscode";
import {
  GatewayClient,
  type Workflow,
} from "../gateway-client";
import { versionRowSpecs, workflowRowSpec } from "../workflow-versions";

export class WorkflowTreeProvider
  implements vscode.TreeDataProvider<WorkflowItem>, vscode.Disposable
{
  private readonly _onDidChange = new vscode.EventEmitter<WorkflowItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private readonly getClient: () => GatewayClient | undefined) {}

  getTreeItem(element: WorkflowItem): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: WorkflowItem): Promise<WorkflowItem[]> {
    if (element) return element.workflow ? versionItems(element.workflow) : [];
    const client = this.getClient();
    if (!client) {
      return [
        new WorkflowItem("Not connected", "", "workflow-offline"),
      ];
    }

    // Public sources have no workflow control plane. Show an intentional
    // placeholder rather than a 404 from the missing /gw/workflows routes.
    if (client.isPublic()) {
      return [
        new WorkflowItem(
          "Not available for public sources",
          "Workflows require a platform (UMRS gateway) source",
          "workflow-unavailable",
        ),
      ];
    }

    let workflows: Workflow[];
    try {
      workflows = await client.listWorkflows();
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : "Check Gateway connection";
      return [
        new WorkflowItem("Error loading workflows", detail, "workflow-error"),
      ];
    }

    if (workflows.length === 0) {
      return [
        new WorkflowItem("No workflows", "", "workflow-empty"),
      ];
    }

    return workflows.map(workflowItem);
  }

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}

/** One workflow row: `name` + `v<default> (+N) · description`. */
function workflowItem(w: Workflow): WorkflowItem {
  const spec = workflowRowSpec(w);
  const item = new WorkflowItem(spec.label, spec.description, "workflow");
  item.workflowId = w.workflow_id;
  item.workflowName = w.name;
  item.workflow = w;
  item.iconPath = new vscode.ThemeIcon("symbol-event");
  if (spec.expandable) item.collapsibleState = vscode.TreeItemCollapsibleState.Collapsed;
  if (w.tags && w.tags.length > 0) {
    item.tooltip = `Tags: ${w.tags.join(", ")}`;
  }
  return item;
}

/** A child per registered version; its trigger runs that version. */
function versionItems(w: Workflow): WorkflowItem[] {
  return versionRowSpecs(w).map((spec) => {
    const item = new WorkflowItem(spec.label, spec.description, "workflow-version");
    item.workflowId = w.workflow_id;
    item.workflowName = w.name;
    item.workflowVersion = spec.version;
    item.iconPath = new vscode.ThemeIcon("tag");
    return item;
  });
}

export class WorkflowItem extends vscode.TreeItem {
  workflowId?: string;
  workflowName?: string;
  /** The catalog row (top-level rows only): its versions feed the picker. */
  workflow?: Workflow;
  /** Set on a version child: the exact version to run. */
  workflowVersion?: string;

  constructor(
    label: string,
    description: string,
    contextValue: string,
  ) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.description = description;
    this.contextValue = contextValue;
  }
}
