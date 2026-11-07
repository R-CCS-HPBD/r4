/**
 * VS Code MCP definitions for R4 Platform tools.
 *
 * Agent Window and Copilot agent mode discover these definitions through
 * VS Code's MCP provider API. Tokens are added only when VS Code resolves a
 * server for launch, so secrets are not persisted in package metadata.
 */

import * as vscode from "vscode";
import { AuthManager } from "./auth";

export const R4_MCP_PROVIDER_ID = "r4";

const DEFAULT_MCP_COMMAND = "r4-mcp";
const PLATFORM_LABEL = "R4 Platform";
const RAG_LABEL = "R4 RAG";
const VISION_LABEL = "R4 Vision";
const MCP_APPS_LABEL = "R4 MCP Apps";

type McpEnv = Record<string, string | number | null>;

export class R4McpDefinitionProvider
  implements vscode.McpServerDefinitionProvider<vscode.McpStdioServerDefinition>, vscode.Disposable
{
  private readonly _onDidChangeMcpServerDefinitions =
    new vscode.EventEmitter<void>();

  readonly onDidChangeMcpServerDefinitions =
    this._onDidChangeMcpServerDefinitions.event;

  constructor(private readonly auth: AuthManager) {}

  refresh(): void {
    this._onDidChangeMcpServerDefinitions.fire();
  }

  async provideMcpServerDefinitions(
    token: vscode.CancellationToken,
  ): Promise<vscode.McpStdioServerDefinition[]> {
    const config = vscode.workspace.getConfiguration("r4");
    if (!config.get<boolean>("mcp.enabled", true)) {
      return [];
    }

    // R4 MCP servers (platform/rag/vision) all talk to the Gateway `/gw/*`
    // control plane. A public source is a plain OpenAI-compatible `/v1`
    // endpoint with no `/gw/mcp` route, so advertising these servers for a
    // public active source only produces MCP `tools/list` calls that never
    // resolve — which stalls Copilot agent mode at "Optimizing tool
    // selection". Expose no R4 MCP servers while a public source is active.
    const kind = await this.auth.getActiveSourceKind();
    if (kind === "public") {
      return [];
    }

    const gatewayUrl = await this.auth.getGatewayUrl();
    const storedToken = await this.auth.getToken();
    if (token.isCancellationRequested || !gatewayUrl || !storedToken) {
      return [];
    }

    const command = this.getMcpCommand(config);
    const timeout = this.getTimeoutSeconds(config);
    const projectId = this.getProjectId(config);
    const env = this.buildBaseEnv(gatewayUrl, projectId);

    const visionModelPath = config.get<string>("mcp.vision.modelPath", "").trim() ||
      process.env.UMRS_SAM3_MODEL_PATH || "";
    const visionDevice = config.get<string>("mcp.vision.device", "cuda").trim() || "cuda";

    const visionArgs = ["vision", "--device", visionDevice];
    if (visionModelPath) {
      visionArgs.push("--model-path", visionModelPath);
    }

    return [
      new vscode.McpStdioServerDefinition(
        PLATFORM_LABEL,
        command,
        ["platform", "--gateway-url", gatewayUrl, "--timeout", String(timeout)],
        env,
        `platform:${gatewayUrl}:${timeout}`,
      ),
      new vscode.McpStdioServerDefinition(
        RAG_LABEL,
        command,
        ["rag-proxy", "--gateway-url", gatewayUrl, "--timeout", String(timeout)],
        env,
        `rag:${gatewayUrl}:${projectId}:${timeout}`,
      ),
      new vscode.McpStdioServerDefinition(
        VISION_LABEL,
        command,
        visionArgs,
        env,
        `vision:${visionModelPath}:${visionDevice}`,
      ),
    ];
  }

  async resolveMcpServerDefinition(
    server: vscode.McpStdioServerDefinition,
    token: vscode.CancellationToken,
  ): Promise<vscode.McpStdioServerDefinition | undefined> {
    const gatewayUrl = await this.auth.getGatewayUrl();
    const apiToken = await this.auth.getToken();
    if (token.isCancellationRequested) {
      return undefined;
    }
    if (!gatewayUrl || !apiToken) {
      void vscode.window.showWarningMessage(
        "R4: Login to Gateway before starting R4 MCP tools.",
      );
      return undefined;
    }

    const projectId = this.getProjectId(vscode.workspace.getConfiguration("r4"));
    server.env = {
      ...server.env,
      ...this.buildBaseEnv(gatewayUrl, projectId),
      R4_GATEWAY_TOKEN: apiToken,
      R4_API_KEY: apiToken,
    };
    return server;
  }

  dispose(): void {
    this._onDidChangeMcpServerDefinitions.dispose();
  }

  private getMcpCommand(config: vscode.WorkspaceConfiguration): string {
    return config.get<string>("mcp.command", DEFAULT_MCP_COMMAND).trim() ||
      DEFAULT_MCP_COMMAND;
  }

  private getProjectId(config: vscode.WorkspaceConfiguration): string {
    return config.get<string>("mcp.projectId", "").trim() ||
      process.env.R4_PROJECT_ID ||
      "";
  }

  private getTimeoutSeconds(config: vscode.WorkspaceConfiguration): number {
    const timeout = config.get<number>("mcp.timeout", 30);
    return Number.isFinite(timeout) && timeout > 0 ? timeout : 30;
  }

  private buildBaseEnv(gatewayUrl: string, projectId: string): McpEnv {
    return {
      R4_GATEWAY_URL: gatewayUrl,
      ...(projectId ? { R4_PROJECT_ID: projectId } : {}),
    };
  }
}

export function registerR4McpDefinitionProvider(
  context: vscode.ExtensionContext,
  provider: R4McpDefinitionProvider,
): boolean {
  context.subscriptions.push(provider);

  const languageModels = vscode.lm as
    | {
      registerMcpServerDefinitionProvider?: typeof vscode.lm.registerMcpServerDefinitionProvider;
    }
    | undefined;
  if (!languageModels?.registerMcpServerDefinitionProvider) {
    return false;
  }

  context.subscriptions.push(
    languageModels.registerMcpServerDefinitionProvider(
      R4_MCP_PROVIDER_ID,
      provider,
    ),
  );
  return true;
}
