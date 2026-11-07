/**
 * Workflow tree view — sidebar panel for browsing and triggering
 * workflows via Gateway /gw/workflows/*.
 */

import * as vscode from "vscode";
import {
  GatewayClient,
  type Workflow,
} from "../gateway-client";

export class WorkflowTreeProvider
  implements vscode.TreeDataProvider<WorkflowItem>, vscode.Disposable
{
  private readonly _onDidChange = new vscode.EventEmitter<WorkflowItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;

  constructor(private readonly getClient: () => GatewayClient | undefined) {}

  getTreeItem(element: WorkflowItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<WorkflowItem[]> {
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

    return workflows.map((w) => {
      const item = new WorkflowItem(
        w.name ?? "",
        w.description ?? "",
        "workflow",
      );
      item.workflowId = w.workflow_id;
      item.workflowName = w.name;
      item.iconPath = new vscode.ThemeIcon("symbol-event");
      if (w.tags && w.tags.length > 0) {
        item.tooltip = `Tags: ${w.tags.join(", ")}`;
      }
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

export class WorkflowItem extends vscode.TreeItem {
  workflowId?: string;
  workflowName?: string;

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
