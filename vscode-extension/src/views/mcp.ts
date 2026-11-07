/**
 * MCP tree view — sidebar panel for browsing MCP servers and tools,
 * and invoking tools via Gateway /gw/mcp/*.
 */

import * as vscode from "vscode";
import {
  GatewayClient,
  type MCPServer,
  type MCPTool,
} from "../gateway-client";

export type McpAppsState = "disconnected" | "connecting" | "active";

type MCPTreeItem = MCPServerItem | MCPToolItem | McpAppsStatusItem;

export class MCPTreeProvider
  implements vscode.TreeDataProvider<MCPTreeItem>, vscode.Disposable
{
  private readonly _onDidChange = new vscode.EventEmitter<MCPTreeItem | undefined>();
  readonly onDidChangeTreeData = this._onDidChange.event;
  private servers: MCPServer[] = [];
  private standaloneTools: MCPTool[] = [];
  private mcpAppsState: McpAppsState = "disconnected";
  private mcpAppsUrl?: string;

  constructor(private readonly getClient: () => GatewayClient | undefined) {}

  getTreeItem(element: MCPTreeItem): vscode.TreeItem {
    if (element instanceof McpAppsStatusItem) {
      const item = new vscode.TreeItem(
        "MCP Apps",
        vscode.TreeItemCollapsibleState.None,
      );
      item.description = element.state === "active"
        ? element.url ? `active · ${new URL(element.url).hostname}` : "active"
        : element.state;
      item.iconPath = new vscode.ThemeIcon(
        element.state === "active" ? "plug" : element.state === "connecting" ? "sync~spin" : "unplug",
      );
      item.contextValue = `mcp-apps-${element.state}`;
      item.tooltip = `MCP Apps SSE: ${element.state}${element.url ? "\n" + element.url : ""}`;
      item.command = {
        command: "r4.activateMcpApps",
        title: "Activate MCP Apps",
      };
      return item;
    }
    return element;
  }

  async getChildren(element?: MCPTreeItem): Promise<MCPTreeItem[]> {
    const client = this.getClient();
    if (!client) {
      return [
        new MCPServerItem("Not connected", "Login first", "mcp-offline", []),
      ];
    }

    // Public sources have no MCP control plane. Show an intentional placeholder
    // rather than surfacing a 404 from the missing /gw/mcp/* routes.
    if (client.isPublic()) {
      return [
        new MCPServerItem(
          "Not available for public sources",
          "MCP requires a platform (UMRS gateway) source",
          "mcp-unavailable",
          [],
        ),
      ];
    }

    // Root level: show McpApps status first, then servers
    if (!element) {
      const statusItems: MCPTreeItem[] =
        [new McpAppsStatusItem(this.mcpAppsState, this.mcpAppsUrl)];

      try {
        const [servers, tools] = await Promise.all([
          client.listMCPServers(),
          client.listMCPTools(),
        ]);
        this.servers = servers;

        // Group tools by server, collect orphans
        const serverToolMap = new Map<string, MCPTool[]>();
        this.standaloneTools = [];
        for (const t of tools) {
          const key = t.server ?? "";
          if (key) {
            const arr = serverToolMap.get(key) ?? [];
            arr.push(t);
            serverToolMap.set(key, arr);
          } else {
            this.standaloneTools.push(t);
          }
        }

        const items: MCPTreeItem[] = [];

        for (const srv of this.servers) {
          const key = srv.name ?? srv.server_id ?? "";
          const srvTools = serverToolMap.get(key) ?? srv.tools ?? [];
          const statusIcon =
            srv.status === "healthy" ? "$(pass)" : srv.status === "unhealthy" ? "$(error)" : "$(question)";
          items.push(
            new MCPServerItem(
              srv.name,
              `${statusIcon} ${srv.status ?? "unknown"} · ${srvTools.length} tools`,
              "mcp-server",
              srvTools,
            ),
          );
        }

        // Show standalone tools that don't belong to any server
        for (const t of this.standaloneTools) {
          items.push(new MCPToolItem(t));
        }

        return [...statusItems, ...items];
      } catch (err: unknown) {
        const detail = err instanceof Error ? err.message : "Check Gateway connection";
        return [
          new MCPServerItem("Error loading MCP", detail, "mcp-error", []),
        ];
      }
    }

    // Children of a server: its tools
    if (element instanceof MCPServerItem) {
      return element.tools.map((t) => new MCPToolItem(t));
    }

    if (element instanceof McpAppsStatusItem) {
      return [];
    }

    return [];
  }

  setMcpAppsState(state: McpAppsState, url?: string): void {
    this.mcpAppsState = state;
    this.mcpAppsUrl = url;
    this.refresh();
  }

  refresh(): void {
    this._onDidChange.fire(undefined);
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}

export class MCPServerItem extends vscode.TreeItem {
  constructor(
    label: string,
    description: string,
    contextValue: string,
    public readonly tools: MCPTool[],
  ) {
    super(
      label,
      (tools?.length ?? 0) > 0
        ? vscode.TreeItemCollapsibleState.Collapsed
        : vscode.TreeItemCollapsibleState.None,
    );
    this.description = description;
    this.contextValue = contextValue;
    this.iconPath = new vscode.ThemeIcon("server-process");
  }
}

export class MCPToolItem extends vscode.TreeItem {
  public readonly toolName: string;
  public readonly toolSchema?: Record<string, unknown>;

  constructor(tool: MCPTool) {
    super(tool.name ?? "", vscode.TreeItemCollapsibleState.None);
    this.toolName = tool.name;
    this.toolSchema = tool.inputSchema;
    this.description = tool.description;
    this.contextValue = "mcp-tool";
    this.iconPath = new vscode.ThemeIcon("symbol-method");
    this.tooltip = new vscode.MarkdownString(
      `**${tool.name}**\n\n${tool.description}${tool.server ? `\n\n*Server: ${tool.server}*` : ""}`,
    );
  }
}

export class McpAppsStatusItem {
  constructor(
    public readonly state: McpAppsState,
    public readonly url?: string,
  ) {}
}
