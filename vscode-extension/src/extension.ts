/**
 * R4 Platform VS Code Extension — main entry point.
 *
 * Wires up: auth, Gateway client, status bar, chat participant,
 * and sidebar tree views. All platform communication goes through
 * Gateway HTTP (no direct UMRS/LiteLLM access).
 */

import * as vscode from "vscode";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { AuthManager } from "./auth";
import { GatewayClient, GatewayError } from "./gateway-client";
import type { ProvisionOptions } from "./gateway-client";
import { StatusBar } from "./status-bar";
import { R4ChatParticipant } from "./chat-participant";
import { createEditSessionController } from "./edit-session";
import { EndpointTreeProvider, EndpointItem } from "./views/endpoints";
import { JobTreeProvider, JobItem } from "./views/jobs";
import { ModelTreeProvider, ModelItem } from "./views/models";
import { MCPTreeProvider, MCPToolItem } from "./views/mcp";
import { WorkflowTreeProvider, WorkflowItem } from "./views/workflows";
import {
  CompanionTreeProvider,
  CompanionCapabilityItem,
  type CompanionCapability,
} from "./views/companion";
import {
  R4McpDefinitionProvider,
  registerR4McpDefinitionProvider,
} from "./mcp-provider";
import { registerR4LanguageModelProvider } from "./lm-provider";
import { setDefaultChatModel } from "./chat-model";
import { configureVisionProxyModel } from "./vision-proxy";
import { AcpProcessSupervisor } from "./acp-process";
import { R4AgentViewProvider } from "./agent-view";
import { createVscodeAgentToolBridge } from "./vscode-agent-tools";
import { consoleTerminalSpec } from "./console-launch";
import { CompanionBridge } from "./bridge/companion-bridge";
import { resolveBridgePython } from "./bridge/resolve-python";
import { ChatPanels } from "./bridge/chat-panels";

let client: GatewayClient | undefined;
let editSessionController: ReturnType<typeof createEditSessionController> | undefined;
let lmProviderRef: import("./lm-provider").R4LanguageModelProvider | undefined;
/** Workspace state for persisting provision tracking across restarts. */
let workspaceState: vscode.Memento | undefined;
/** In-memory lock for in-flight provisions within same extension host.
 *  Prevents TOCTOU races between two VSCode windows sharing workspaceState. */
const provisioningLocks = new Set<string>();
/** Whether the last reconcileProvisioningState call succeeded. When false,
 *  the next endpoint tree refresh will retry reconciliation. */
let reconcileOk = true;

/** Resolve path to the r4 CLI executable.
 *
 * 1. Uses `r4.cliPath` setting if explicitly overridden.
 * 2. If left at default ("r4"), probes common install locations.
 * 3. Falls back to "r4" and hopes the shell PATH resolves it.
 */
function resolveCliPath(): string {
  const cfg = vscode.workspace.getConfiguration("r4").get<string>("cliPath", "r4");
  if (cfg && cfg !== "r4") {
    return cfg;
  }
  const home = os.homedir();
  const candidates = [
    path.join(home, ".r4", "venv", "bin", "r4"),
    path.join(home, ".local", "bin", "r4"),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) {
      return c;
    }
  }
  return "r4";
}

export function activate(context: vscode.ExtensionContext): void {
  // ── TLS configuration for internal/self-signed certs ────────
  applyTlsConfig();

  const auth = new AuthManager(context.secrets);
  const getClient = (): GatewayClient | undefined => client;
  workspaceState = context.workspaceState;

  // Build a GatewayClient targeting a specific source by name (platform
  // gateway or public OpenAI-compatible endpoint). Used when acting on a tree
  // item that belongs to a non-active source (e.g. provisioning an rccs model
  // while the active source is ai4s).
  const clientForSource = (sourceName?: string): GatewayClient | undefined => {
    if (!sourceName) {
      return getClient();
    }
    const cfg = auth.listLlmSourceConfigs().find((s) => s.name === sourceName);
    if (!cfg) {
      return getClient();
    }
    return new GatewayClient({ baseUrl: cfg.url, token: cfg.token, kind: cfg.kind });
  };

  // ── Initialise client from stored credentials ──────────────

  void initClient(auth).then(() => statusBar.refresh());

  // ── Status bar ─────────────────────────────────────────────

  const statusBar = new StatusBar(auth, getClient);
  context.subscriptions.push(statusBar);

  const refreshInterval = vscode.workspace
    .getConfiguration("r4")
    .get<number>("autoRefreshInterval", 30);
  statusBar.startPolling(refreshInterval * 1000);

  // ── Chat participant ───────────────────────────────────────

  const chatParticipant = new R4ChatParticipant(getClient);
  context.subscriptions.push(chatParticipant);

  // ── Dedicated R4 Agent view ────────────────────────────────

  registerAgentView(context, auth);

  // ── Agent Window / Copilot MCP tools ───────────────────────

  const mcpDefinitionProvider = new R4McpDefinitionProvider(auth);
  registerR4McpDefinitionProvider(context, mcpDefinitionProvider);

  // ── Native language-model provider (Copilot model picker) ──

  const getSourceLabel = (): string => {
    try {
      // For multi-source setups, include the active source name so users
      // can tell R4 models apart from built-in Copilot models.
      const sources = auth.listLlmSourceConfigs();
      if (sources.length > 1) {
        const active = auth.getActiveSourceNameSync() || "R4";
        return active;
      }
      return "";
    } catch {
      return "";
    }
  };
  const lmProvider = registerR4LanguageModelProvider(
    context,
    getClient,
    () => auth.listLlmSourceConfigs(),
    getSourceLabel,
  );
  lmProviderRef = lmProvider;

  // ── Tree views ─────────────────────────────────────────────

  const endpointTree = new EndpointTreeProvider(getClient, auth, async () => {
    // Retry reconciliation on each tree refresh if previous attempt failed
    if (!reconcileOk && workspaceState && client) {
      try {
        await reconcileProvisioningState(workspaceState, client);
      } catch {
        // Still failing — will retry on next refresh
      }
    }
  });
  const jobTree = new JobTreeProvider(getClient);
  const modelTree = new ModelTreeProvider(getClient, auth);
  const mcpTree = new MCPTreeProvider(getClient);
  const workflowTree = new WorkflowTreeProvider(getClient);
  const companionTree = new CompanionTreeProvider(getClient);

  context.subscriptions.push(
    vscode.window.registerTreeDataProvider("r4.endpoints", endpointTree),
    vscode.window.registerTreeDataProvider("r4.jobs", jobTree),
    vscode.window.registerTreeDataProvider("r4.models", modelTree),
    vscode.window.registerTreeDataProvider("r4.mcp", mcpTree),
    vscode.window.registerTreeDataProvider("r4.workflows", workflowTree),
    vscode.window.registerTreeDataProvider("r4.companion", companionTree),
    endpointTree,
    jobTree,
    modelTree,
    mcpTree,
    workflowTree,
    companionTree,
  );

  if (refreshInterval > 0) {
    endpointTree.startPolling(refreshInterval * 1000);
    jobTree.startPolling(refreshInterval * 1000);
  }

  // ── Commands ───────────────────────────────────────────────

  context.subscriptions.push(
    vscode.commands.registerCommand("r4.login", async () => {
      const ok = await auth.login();
      if (ok) {
        await initClient(auth);
        statusBar.refresh();
        endpointTree.refresh();
        jobTree.refresh();
        modelTree.refresh();
        mcpTree.refresh();
        workflowTree.refresh();
        companionTree.refresh();
        mcpDefinitionProvider.refresh();
        lmProvider?.refresh();
        vscode.window.showInformationMessage("R4: Connected to Gateway.");
      }
    }),

    vscode.commands.registerCommand("r4.activateMcpApps", async () => {
      const c = getClient();
      if (!c) {
        vscode.window.showWarningMessage("R4: Not connected. Login first.");
        return;
      }
      mcpTree.setMcpAppsState("connecting");
      try {
        const { sseUrl, status } = await c.activateMcpAppsSSE();
        if (status === "active") {
          mcpTree.setMcpAppsState("active", sseUrl);
          vscode.window.showInformationMessage(
            `R4 MCP Apps active. SSE URL: ${sseUrl}`,
          );
        } else {
          mcpTree.setMcpAppsState("disconnected");
          vscode.window.showWarningMessage(
            `R4 MCP Apps status: ${status}`,
          );
        }
      } catch (err) {
        mcpTree.setMcpAppsState("disconnected");
        vscode.window.showErrorMessage(
          `R4 MCP Apps activation failed: ${(err as Error).message}`,
        );
      }
    }),

    vscode.commands.registerCommand("r4.mcpInstall", async () => {
      const uris = await vscode.window.showOpenDialog({
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Select MCP Server Directory",
        title: "Stage Custom MCP Server",
      });
      if (!uris || uris.length === 0) {
        return;
      }
      const dir = uris[0].fsPath;
      const name = await vscode.window.showInputBox({
        prompt: "Server name for registration",
        placeHolder: "my-custom-mcp",
        validateInput: (v) =>
          /^[a-z0-9_-]+$/.test(v) ? null : "Use lowercase, numbers, hyphens, underscores only",
      });
      if (!name) {
        return;
      }
      const terminal =
        vscode.window.activeTerminal ??
        vscode.window.createTerminal("R4 MCP Install");
      terminal.show();
      terminal.sendText(
        `cd "${dir}" && ${resolveCliPath()} mcp install --name ${name} .`,
        true,
      );
    }),

    vscode.commands.registerCommand("r4.logout", async () => {
      await auth.logout();
      client = undefined;
      statusBar.refresh();
      endpointTree.refresh();
      jobTree.refresh();
      modelTree.refresh();
      mcpTree.refresh();
      workflowTree.refresh();
      companionTree.refresh();
      mcpDefinitionProvider.refresh();
      lmProvider?.refresh();
    }),

    vscode.commands.registerCommand("r4.switchSource", async () => {
      const sources = auth.listPlatformSources();
      if (sources.length === 0) {
        vscode.window.showInformationMessage(
          "R4: No platform sources found. Add one with: r4 source add <name> --url <url> --kind platform",
        );
        return;
      }
      const currentName = await auth.getActiveSourceName();
      const accountName = await auth.getAccountName();
      const pick = await vscode.window.showQuickPick(
        sources.map((s) => ({
          label: s.name,
          description: s.url ?? s.gateway_url ?? "",
          detail: [
            currentName === s.name ? "(current)" : "",
            accountName ? `account: ${accountName}` : "",
          ].filter(Boolean).join("  ·  "),
        })),
        { title: "Switch R4 Source" },
      );
      if (!pick) { return; }

      const source = await auth.switchSource(pick.label);
      if (source) {
        // Some sources may share the same token, others may need re-auth.
        // Try to init client with existing token first.
        await initClient(auth);
        if (!client) {
          // Need credentials — prompt login for this source
          const ok = await auth.login();
          if (ok) {
            await initClient(auth);
          }
        }
        statusBar.refresh();
        endpointTree.refresh();
        jobTree.refresh();
        modelTree.refresh();
        mcpTree.refresh();
        workflowTree.refresh();
        companionTree.refresh();
        mcpDefinitionProvider.refresh();
        lmProvider?.refresh();
      }
    }),

    vscode.commands.registerCommand("r4.addSource", async () => {
      // Register a public (OpenAI-compatible) LLM source by writing to the
      // shared R4 CLI sources.json. For platform sources use `r4 source add
      // --kind platform` from the CLI (it also syncs the umrs ZMQ profile).
      const name = await vscode.window.showInputBox({
        title: "Add Public Source (1/4)",
        prompt: "Source name (e.g. ollama, claude, openrouter)",
        validateInput: (v) => {
          if (!v.trim()) { return "Name is required"; }
          if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(v.trim())) {
            return "Use letters, digits, '.', '_', '-' (start with letter/digit)";
          }
          return undefined;
        },
      });
      if (name === undefined) { return; }

      const url = await vscode.window.showInputBox({
        title: "Add Public Source (2/4)",
        prompt: "Base URL of the OpenAI-compatible endpoint",
        value: "http://localhost:11434",
        validateInput: (v) => {
          try {
            const u = new URL(v.trim());
            return /^https?:$/.test(u.protocol) ? undefined : "Use http:// or https://";
          } catch {
            return "Not a valid URL";
          }
        },
      });
      if (url === undefined) { return; }

      const apiKey = await vscode.window.showInputBox({
        title: "Add Public Source (3/4)",
        prompt: "API key — leave empty for unauthenticated endpoints (e.g. local Ollama)",
        password: true,
      });
      if (apiKey === undefined) { return; }

      const providerPick = await vscode.window.showQuickPick(
        [
          { label: "openai-compatible", provider: undefined, description: "Ollama, LM Studio, vLLM, LiteLLM, OpenRouter…" },
          { label: "ollama", provider: "ollama", description: "Local Ollama daemon" },
          { label: "anthropic", provider: "anthropic", description: "Anthropic native endpoint" },
          { label: "other…", provider: "", description: "Type a provider name" },
        ],
        { title: "Add Public Source (4/4)" },
      );
      if (!providerPick) { return; }
      let provider = providerPick.provider;
      let description: string | undefined;
      if (provider === "") {
        provider = (await vscode.window.showInputBox({
          title: "Provider name",
          prompt: "e.g. bedrock — leave empty to use OpenAI-compatible",
        }))?.trim() || undefined;
        if (provider === undefined) { return; }
      }
      if (provider === "ollama") {
        description = "Local Ollama";
      }

      const source = {
        name: name.trim(),
        kind: "public",
        url: url.trim().replace(/\/+$/, ""),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        ...(provider ? { provider } : {}),
        ...(description ? { description } : {}),
      };
      try {
        const result = auth.upsertCliSource(source);
        if (result.activated) {
          await auth.switchSource(source.name);
        }
        const verb = result.updated ? "Updated" : "Added";
        vscode.window.showInformationMessage(
          `R4: ${verb} public source '${source.name}' (${source.url})` +
            (result.activated ? " — set as active source" : ""),
        );
      } catch (err) {
        vscode.window.showErrorMessage(
          `R4: Failed to register source '${source.name}': ${err instanceof Error ? err.message : String(err)}`,
        );
        return;
      }
      statusBar.refresh();
      modelTree.refresh();
      endpointTree.refresh();
      lmProvider?.refresh();
    }),

    vscode.commands.registerCommand("r4.selectModel", async () => {
      const c = getClient();
      if (!c) {
        vscode.window.showWarningMessage("R4: Not connected. Login first.");
        return;
      }
      try {
        const models = await c.listModels();
        if (models.length === 0) {
          vscode.window.showInformationMessage("R4: No models available.");
          return;
        }
        const pick = await vscode.window.showQuickPick(
          models.map((m) => ({
            label: m.name,
            description: `${m.scope}${m.model_type ? ` · ${m.model_type}` : ""}`,
          })),
          { title: "Select Default Model" },
        );
        if (pick) {
          await setDefaultChatModel(pick.label, statusBar);
        }
      } catch (err) {
        showGatewayError(err);
      }
    }),

    vscode.commands.registerCommand("r4.modelUp", async (item?: ModelItem) => {
      const c = clientForSource(item?.modelSource);
      if (!c || !item?.modelName) { return; }

      const isAdmin = await auth.isAdmin().catch(() => false);
      const opts = await collectProvisionOptions(c, item.modelName, {
        state: context.workspaceState,
        isAdmin,
        // Seed the access level from the model scope so provisioning a
        // system/project/user model defaults to a matching endpoint tier.
        defaultTier: item.modelScope,
      });
      if (!opts) { return; }

      try {
        await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: `R4: Provisioning ${item.modelName}…`,
            cancellable: false,
          },
          async () => {
            await c.provisionEndpoint(opts);
          },
        );
        vscode.window.showInformationMessage(
          `R4: Endpoint provisioning started for ${item.modelName}`,
        );
        endpointTree.refresh();
        jobTree.refresh();
      } catch (err) {
        showGatewayError(err);
      }
    }),

    vscode.commands.registerCommand("r4.poolUp", async () => {
      const uris = await vscode.window.showOpenDialog({
        canSelectFiles: true,
        canSelectFolders: false,
        canSelectMany: false,
        openLabel: "Select Pool Roster",
        title: "Launch Model Pool",
        filters: { "Pool roster": ["yaml", "yml"], "All files": ["*"] },
      });
      if (!uris || uris.length === 0) { return; }
      const file = uris[0].fsPath;

      const confirm = await vscode.window.showWarningMessage(
        `Launch all models defined in "${file}"? This provisions one endpoint per pool entry.`,
        { modal: true },
        "Launch Pool",
      );
      if (confirm !== "Launch Pool") { return; }

      // Delegate to the canonical `r4 pool up` CLI so pool sizing/semantics
      // stay in a single source of truth (no divergent reimplementation here).
      const terminal =
        vscode.window.activeTerminal ??
        vscode.window.createTerminal("R4 Pool");
      terminal.show();
      terminal.sendText(`${resolveCliPath()} admin pool up "${file}"`, true);
    }),

    vscode.commands.registerCommand("r4.modelRegister", async () => {
      // Delegate model registration to the CLI so path validation, scope
      // assignment, and duplicate detection stay in a single source of truth.
      const name = await vscode.window.showInputBox({
        title: "Register Model",
        prompt: "Model name (must be unique within scope)",
        placeHolder: "e.g. Qwen/Qwen2.5-7B-Instruct",
      });
      if (!name) { return; }

      const uris = await vscode.window.showOpenDialog({
        canSelectFiles: false,
        canSelectFolders: true,
        canSelectMany: false,
        openLabel: "Select Model Directory",
        title: `Register Model: ${name}`,
      });
      if (!uris || uris.length === 0) { return; }
      const modelPath = uris[0].fsPath;

      const scopePick = await vscode.window.showQuickPick(
        [
          { label: "User", description: "Private to you", _scope: "user" },
          { label: "Project", description: "Shared with your project", _scope: "project" },
          { label: "System", description: "Platform-wide (admin only)", _scope: "system" },
        ],
        { title: "Model Scope" },
      );
      if (!scopePick) { return; }

      const typePick = await vscode.window.showQuickPick(
        [
          { label: "LLM", description: "Large language model (vLLM/SGLang)", _type: "llm" },
          { label: "Vision", description: "Vision / VLM (BentoML)", _type: "vision" },
          { label: "Embedding", description: "Text embedding model", _type: "embedding" },
        ],
        { title: "Model Type" },
      );
      if (!typePick) { return; }

      const terminal =
        vscode.window.activeTerminal ??
        vscode.window.createTerminal("R4 Model Register");
      terminal.show();
      const cmd = `${resolveCliPath()} model register "${name}" "${modelPath}"` +
        ` --scope ${scopePick._scope} --type ${typePick._type}`;
      terminal.sendText(cmd, true);
      // Refresh the model tree after a short delay so the CLI has time
      // to send the register request.
      setTimeout(() => modelTree.refresh(), 2000);
      vscode.window.showInformationMessage(
        `R4: Registering model "${name}" in terminal…`,
      );
    }),

    vscode.commands.registerCommand("r4.modelStage", async () => {
      const huggingfaceId = await vscode.window.showInputBox({
        title: "Stage Model from Hugging Face",
        prompt: "Hugging Face repo ID (e.g. Qwen/Qwen2.5-7B-Instruct)",
        placeHolder: "org/model-name",
      });
      if (!huggingfaceId) { return; }

      const scopePick = await vscode.window.showQuickPick(
        [
          { label: "User", description: "Private to you", _scope: "user" },
          { label: "Project", description: "Shared with your project", _scope: "project" },
          { label: "System", description: "Platform-wide (admin only)", _scope: "system" },
        ],
        { title: "Model Scope" },
      );
      if (!scopePick) { return; }

      const terminal =
        vscode.window.activeTerminal ??
        vscode.window.createTerminal("R4 Model Stage");
      terminal.show();
      terminal.sendText(
        `${resolveCliPath()} model stage "${huggingfaceId}" --scope ${scopePick._scope}`,
        true,
      );
      setTimeout(() => {
        modelTree.refresh();
        jobTree.refresh();
      }, 2000);
      vscode.window.showInformationMessage(
        `R4: Staging model "${huggingfaceId}" — track progress in Jobs panel.`,
      );
    }),

    vscode.commands.registerCommand("r4.modelDown", async (item?: ModelItem) => {
      const c = clientForSource(item?.modelSource);
      if (!c || !item?.modelName) { return; }

      // Admin check for system models
      if (item.modelScope === "system") {
        const isAdmin = await auth.isAdmin();
        if (!isAdmin) {
          vscode.window.showErrorMessage(
            "R4: System endpoints can only be stopped by admins.",
          );
          return;
        }
      }

      // List endpoints matching this model
      try {
        const endpoints = await c.listEndpoints();
        const matching = endpoints.filter((ep) => ep.model === item.modelName);

        if (matching.length === 0) {
          vscode.window.showInformationMessage(
            `R4: No active endpoints found for ${item.modelName}`,
          );
          return;
        }

        const picked = await vscode.window.showQuickPick(
          matching.map((ep) => ({
            label: ep.job_id,
            description: `${ep.status} · ${ep.backend}${ep.host ? ` · ${ep.host}` : ""}`,
          })),
          { title: `Select endpoint to teardown (${item.modelName})` },
        );
        if (!picked) { return; }

        const confirm = await vscode.window.showWarningMessage(
          `Teardown endpoint ${picked.label}?`,
          { modal: true },
          "Teardown",
        );
        if (confirm !== "Teardown") { return; }

        await c.teardownEndpoint(picked.label);
        vscode.window.showInformationMessage("R4: Endpoint teardown requested.");
        endpointTree.refresh();
      } catch (err) {
        showGatewayError(err);
      }
    }),

    vscode.commands.registerCommand("r4.refreshEndpoints", () => {
      endpointTree.refresh();
    }),

    vscode.commands.registerCommand("r4.refreshJobs", () => {
      jobTree.refresh();
    }),

    vscode.commands.registerCommand("r4.refreshModels", () => {
      modelTree.refresh();
    }),

    vscode.commands.registerCommand("r4.endpointUp", async () => {
      const c = getClient();
      if (!c) {
        vscode.window.showWarningMessage("R4: Not connected. Login first.");
        return;
      }

      // Show current source and offer to switch if multiple sources exist
      const sources = auth.listPlatformSources();
      const currentSource = await auth.getActiveSourceName();
      let sourceHint = "";
      if (currentSource) {
        sourceHint = `Source: ${currentSource}`;
      }

      // Try to offer registered models as quick-pick, fall back to free text
      let model: string | undefined;
      let modelScope: string | undefined;
      let modelMaxLen: number | undefined;
      try {
        const models = await c.listModels();
        if (models.length > 0) {
          const items: (vscode.QuickPickItem & { _model?: import("./gateway-client").Model })[] = [
            ...models.map((m) => ({
              label: m.name,
              description: `${m.scope}${m.model_type ? ` · ${m.model_type}` : ""}${
                m.max_model_len ? ` · ctx ${formatCtx(m.max_model_len)}` : ""
              }`,
              _model: m,
            })),
            { label: "$(edit) Enter custom model name…", description: "" },
          ];
          // If multiple sources, add a switch-source option
          if (sources.length > 1) {
            items.push({
              label: "$(arrow-swap) Switch source for provisioning…",
              description: currentSource
                ? `Current: ${currentSource}`
                : `${sources.length} sources available`,
            });
          }
          const picked = await vscode.window.showQuickPick(
            items,
            { title: `Provision Endpoint${sourceHint ? ` — ${sourceHint}` : ""}` },
          );
          if (!picked) { return; }
          if (picked.label.startsWith("$(arrow-swap)")) {
            // Let user switch source first, then re-invoke
            await vscode.commands.executeCommand("r4.switchSource");
            await vscode.commands.executeCommand("r4.endpointUp");
            return;
          }
          if (picked.label.startsWith("$(edit)")) {
            model = await vscode.window.showInputBox({
              title: "Provision Endpoint",
              prompt: "Model name to provision",
              placeHolder: "e.g. Qwen/Qwen2.5-7B-Instruct",
            });
          } else {
            model = picked.label;
            modelScope = picked._model?.scope;
            modelMaxLen = picked._model?.max_model_len;
          }
        }
      } catch {
        // Gateway unreachable or no models endpoint — fall through to manual
      }

      if (!model) {
        model = await vscode.window.showInputBox({
          title: "Provision Endpoint",
          prompt: "Model name to provision",
          placeHolder: "e.g. Qwen/Qwen2.5-7B-Instruct",
        });
      }
      if (!model) { return; }

      const isAdmin = await auth.isAdmin().catch(() => false);
      const opts = await collectProvisionOptions(c, model, {
        state: context.workspaceState,
        isAdmin,
        defaultTier: modelScope,
        modelMaxLen,
      });
      if (!opts) { return; }

      // ── Race guard key (declared early for pre-flight early returns) ──
      const provisioningKey = `r4.provisioning.${model}`;

      // ── Pre-flight: warn if model is still staging ──
      try {
        const catalogModels = await c.listModels();
        const registered = catalogModels.find(
          (m) => m.name === model || m.id === model,
        );
        const modelStatus = registered?.status?.toLowerCase();
        if (modelStatus === "staging") {
          const proceed = await vscode.window.showWarningMessage(
            `R4: Model "${model}" is still staging (downloading). Provisioning now may use an incomplete model.`,
            { modal: true },
            "Provision Anyway",
          );
          if (proceed !== "Provision Anyway") {
            return;
          }
        }
      } catch {
        // Can't check catalog — proceed (backend will validate model path)
      }

      // ── Race guard: prevent duplicate provision for same model ──

      // In-memory lock: prevent same-host TOCTOU (two windows, same workspace)
      if (provisioningLocks.has(provisioningKey)) {
        void vscode.window.showInformationMessage(
          `R4: Endpoint for ${model} is already being provisioned in another window.`,
        );
        return;
      }
      provisioningLocks.add(provisioningKey);

      const existingJobId = context.workspaceState.get<string>(provisioningKey);
      if (existingJobId) {
        // Check if the tracked job is still provisioning or ready
        try {
          const endpoints = await c.listEndpoints();
          const existing = endpoints.find(
            (ep) => ep.job_id === existingJobId && ep.status.toLowerCase() !== "failed" && ep.status.toLowerCase() !== "terminated",
          );
          if (existing) {
            const detail = existing.detail || existing.status;
            void vscode.window.showInformationMessage(
              `R4: Endpoint for ${model} is already ${existing.status} (${detail || "no detail"}). Waiting for it to become ready.`,
            );
            endpointTree.refresh();
            jobTree.refresh();
            provisioningLocks.delete(provisioningKey);
            return;
          }
        } catch {
          // Can't verify — proceed with fresh provision but clear stale key
        }
        // Stale key: job no longer exists. Clear it and proceed.
        void context.workspaceState.update(provisioningKey, undefined);
      }

      // Track this model as in-flight so concurrent/restart re-submits are blocked
      void context.workspaceState.update(provisioningKey, "pending");

      // Track the provision job_id so we can cancel it if the user aborts.
      let provisionJobId: string | undefined;

      try {
        // Phase 1: Submit provision (202 Accepted) — cancellable
        const provisionResult = await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: `R4: Submitting provision for ${model}…`,
            cancellable: true,
          },
          async (_progress, cancelToken) => {
            const result = await c.provisionEndpoint(opts);
            // If user hit cancel during the request, tear down
            if (cancelToken.isCancellationRequested && result.job_id) {
              try {
                await c.teardownEndpoint(result.job_id, true);
              } catch {
                // Best-effort cleanup
              }
              throw new Error("Provision cancelled by user");
            }
            return result;
          },
        );
        provisionJobId = provisionResult.job_id;
        if (provisionJobId) {
          void context.workspaceState.update(provisioningKey, provisionJobId);
        }
        endpointTree.refresh();
        jobTree.refresh();

        // Phase 2: Poll for readiness with progressive backoff — cancellable
        await vscode.window.withProgress(
          {
            location: vscode.ProgressLocation.Notification,
            title: `R4: Waiting for ${model} to be ready…`,
            cancellable: true,
          },
          async (progress, cancelToken) => {
            let lastDetail = "";
            await c.waitForEndpointReady(
              model,
              provisionJobId ?? undefined,
              600_000,
              (ep) => {
                // Check cancellation on each poll cycle
                if (cancelToken.isCancellationRequested) {
                  throw new Error("Provision cancelled by user");
                }
                const detail = ep.detail ?? ep.status;
                if (detail !== lastDetail) {
                  lastDetail = detail;
                  progress.report({ message: detail });
                }
                endpointTree.refresh();
                jobTree.refresh();
              },
            );
          },
        );

        vscode.window.showInformationMessage(
          `R4: Endpoint ${model} is ready!`,
        );
        endpointTree.refresh();
        jobTree.refresh();
      } catch (err) {
        // Clear the race-guard key so user can retry
        void context.workspaceState.update(provisioningKey, undefined);
        provisioningLocks.delete(provisioningKey);

        // User cancellation: already handled with teardown above, just show info
        if (err instanceof Error && err.message === "Provision cancelled by user") {
          vscode.window.showInformationMessage(
            `R4: Provisioning cancelled for ${model}.`,
          );
          endpointTree.refresh();
          jobTree.refresh();
          return;
        }

        showGatewayError(err);
      }
    }),

    vscode.commands.registerCommand("r4.endpointDown", async (item?: EndpointItem) => {
      const c = clientForSource(item?.endpointSource);
      if (!c || !item?.endpointId) { return; }

      // Admin restriction: non-admin users cannot teardown system endpoints
      if (item.ownerType === "system" && !item.isAdminView) {
        vscode.window.showErrorMessage(
          "R4: System endpoints can only be stopped by admins.",
        );
        return;
      }

      const confirm = await vscode.window.showWarningMessage(
        `Teardown endpoint ${item.label}?`,
        { modal: true },
        "Teardown",
      );
      if (confirm !== "Teardown") { return; }

      try {
        await c.teardownEndpoint(item.endpointId);
        vscode.window.showInformationMessage("R4: Endpoint teardown requested.");
        endpointTree.refresh();
      } catch (err) {
        showGatewayError(err);
      }
    }),

    vscode.commands.registerCommand("r4.endpointCancel", async (item?: EndpointItem) => {
      const c = clientForSource(item?.endpointSource);
      if (!c || !item?.endpointId) { return; }

      // Admin restriction: non-admin users cannot cancel system endpoints.
      if (item.ownerType === "system" && !item.isAdminView) {
        vscode.window.showErrorMessage(
          "R4: System endpoints can only be stopped by admins.",
        );
        return;
      }

      const confirm = await vscode.window.showWarningMessage(
        `Cancel provisioning of ${item.label}? This kills the launching Slurm/K8s job.`,
        { modal: true },
        "Cancel Provisioning",
      );
      if (confirm !== "Cancel Provisioning") { return; }

      try {
        // force=true issues an immediate hard cancel so the still-launching
        // backend job is killed right away.
        await c.teardownEndpoint(item.endpointId, true);
        vscode.window.showInformationMessage(
          "R4: Provisioning cancelled — backend job terminated.",
        );
        endpointTree.refresh();
        jobTree.refresh();
      } catch (err) {
        showGatewayError(err);
      }
    }),

    vscode.commands.registerCommand("r4.useEndpoint", async (item?: EndpointItem | string) => {
      // Robust invocation. Three call shapes must all work:
      //  - EndpointItem from the Endpoints tree (has modelName)
      //  - a plain endpoint id / model-name string (command link, e.g. from
      //    chat, or programmatic executeCommand)
      //  - no argument at all (Command Palette) → fall back to the picker
      if (typeof item === "string") {
        const modelName = await resolveEndpointModel(getClient, item);
        if (modelName) {
          await setDefaultChatModel(modelName, statusBar);
        } else {
          vscode.window.showWarningMessage(
            `R4: No ready endpoint matches "${item}".`,
          );
        }
        return;
      }
      if (item?.modelName) {
        await setDefaultChatModel(item.modelName, statusBar);
        return;
      }
      await handleSelectEndpoint(getClient, statusBar);
    }),

    vscode.commands.registerCommand("r4.selectEndpoint", async () => {
      await handleSelectEndpoint(getClient, statusBar);
    }),

    vscode.commands.registerCommand("r4.jobCancel", async (item?: JobItem) => {
      const c = getClient();
      if (!c || !item?.jobId) { return; }

      try {
        await c.cancelJob(item.jobId);
        vscode.window.showInformationMessage("R4: Job cancel requested.");
        jobTree.refresh();
      } catch (err) {
        showGatewayError(err);
      }
    }),

    vscode.commands.registerCommand("r4.jobLogs", async (item?: JobItem) => {
      const c = getClient();
      if (!c || !item?.jobId) { return; }

      try {
        const logs = await c.getJobLogs(item.jobId);
        const doc = await vscode.workspace.openTextDocument({
          content: logs || "(no logs available)",
          language: "log",
        });
        await vscode.window.showTextDocument(doc, { preview: true });
      } catch (err) {
        showGatewayError(err);
      }
    }),

    // ── MCP commands ───────────────────────────────────────────

    vscode.commands.registerCommand("r4.refreshMCP", () => {
      mcpTree.refresh();
      mcpDefinitionProvider.refresh();
    }),

    vscode.commands.registerCommand("r4.callTool", async (item?: MCPToolItem) => {
      await handleCallTool(getClient, item);
    }),

    // ── Workflow commands ──────────────────────────────────────

    vscode.commands.registerCommand("r4.refreshWorkflows", () => {
      workflowTree.refresh();
    }),

    vscode.commands.registerCommand("r4.triggerWorkflow", async (item?: WorkflowItem) => {
      await handleTriggerWorkflow(getClient, workflowTree, item);
    }),

    vscode.commands.registerCommand("r4.cancelWorkflow", async () => {
      const c = getClient();
      if (!c) {
        vscode.window.showWarningMessage("R4: Not connected. Login first.");
        return;
      }

      const execId = await vscode.window.showInputBox({
        title: "Cancel Workflow Execution",
        prompt: "Execution ID",
      });
      if (!execId) { return; }

      try {
        await c.cancelWorkflowExecution(execId);
        vscode.window.showInformationMessage("R4: Workflow cancellation requested.");
        workflowTree.refresh();
      } catch (err) {
        showGatewayError(err);
      }
    }),

    // ── Companion commands ─────────────────────────────────────

    vscode.commands.registerCommand("r4.companionChat", async (capability?: CompanionCapability) => {
      if (!capability) { return; }
      // Open VS Code Chat with the @r4 participant and companion command
      void vscode.commands.executeCommand(
        "workbench.action.chat.open",
        { query: `@r4 /companion ${capability.id} `, isPartialQuery: true },
      );
    }),

    vscode.commands.registerCommand("r4.refreshCompanion", () => {
      companionTree.refresh();
    }),

    vscode.commands.registerCommand("r4.configureVisionProxy", async () => {
      await configureVisionProxyModel(getClient());
    }),

    // ── R4-native chat keybindings ─────────────────────────────

    vscode.commands.registerCommand("r4.focusChat", () => {
      void vscode.commands.executeCommand("workbench.action.chat.open");
    }),

    vscode.commands.registerCommand("r4.insertFileRef", () => {
      const editor = vscode.window.activeTextEditor;
      if (!editor) { return; }

      const doc = editor.document;
      const sel = editor.selection;
      const relativePath = vscode.workspace.asRelativePath(doc.uri);

      let ref: string;
      if (sel.isEmpty) {
        const line = sel.active.line + 1;
        ref = `@${relativePath}#L${line}`;
      } else {
        const startLine = sel.start.line + 1;
        const endLine = sel.end.line + 1;
        ref = startLine === endLine
          ? `@${relativePath}#L${startLine}`
          : `@${relativePath}#L${startLine}-L${endLine}`;
      }

      void vscode.env.clipboard.writeText(ref).then(() => {
        vscode.window.showInformationMessage(`Copied: ${ref}`);
      });
    }),

    // B15: Handle tool-call anchor clicks (open detail view).
    vscode.commands.registerCommand(
      "r4.agent.viewToolCall",
      async (toolName: string) => {
        await chatParticipant.handleToolCallDetail(toolName);
      },
    ),
  );

  // ── React to config changes ────────────────────────────────

  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration(async (e) => {
      if (e.affectsConfiguration("r4.gatewayUrl") || e.affectsConfiguration("r4.profile")) {
        await initClient(auth);
        statusBar.refresh();
        endpointTree.refresh();
        jobTree.refresh();
        modelTree.refresh();
        mcpTree.refresh();
        workflowTree.refresh();
        companionTree.refresh();
        mcpDefinitionProvider.refresh();
      }
      if (e.affectsConfiguration("r4.mcp")) {
        mcpDefinitionProvider.refresh();
      }
      if (e.affectsConfiguration("r4.autoRefreshInterval")) {
        const interval = vscode.workspace
          .getConfiguration("r4")
          .get<number>("autoRefreshInterval", 30);
        statusBar.startPolling(interval * 1000);
        endpointTree.startPolling(interval * 1000);
        jobTree.startPolling(interval * 1000);
      }
      if (e.affectsConfiguration("r4.tlsSkipVerify")) {
        applyTlsConfig();
      }
    }),
  );
}

export function deactivate(): void {
  void lmProviderRef?.prepareForDeactivate();
  client = undefined;
}

function registerAgentView(context: vscode.ExtensionContext, auth: AuthManager): void {
  const editSession = createEditSessionController(context);
  editSessionController = editSession;
  const agentView = new R4AgentViewProvider(
    // cwd = workspace root: companion file/shell tools resolve relative paths
    // (file_write etc.) against it; without this they inherit the extension
    // host cwd and writes land in /tmp.
    () =>
      new AcpProcessSupervisor({
        command: resolveCliPath(),
        cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
      }),
    () => client,
    () => createVscodeAgentToolBridge(context, editSession),
    context.extensionUri,
    () => auth.listLlmSourceConfigs(),
  );
  agentView.setEditSession(editSession);
  agentView.setBridgeFactory(() => createCompanionBridge());
  // ADR-103 VP-3b: chat tabs, each with its own bridge (the console's tab strip).
  const chatTabs = new ChatPanels(context.extensionUri, createCompanionBridge, () => agentView.startupMessages());
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(R4AgentViewProvider.viewType, agentView, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.commands.registerCommand("r4.focusAgent", () =>
      vscode.commands.executeCommand(`${R4AgentViewProvider.viewType}.focus`),
    ),
    vscode.commands.registerCommand("r4.openConsole", openConsoleInEditor),
    vscode.commands.registerCommand("r4.tab.new", () => chatTabs.open()),
    vscode.commands.registerCommand("r4.tab.reopen", () => chatTabs.reopen()),
    // `/tab <verb>` from any composer (host LOCAL_SLASH); not in the palette.
    vscode.commands.registerCommand("r4.tab.command", (args?: unknown) => chatTabs.command(String(args ?? ""))),
    vscode.commands.registerCommand("r4.tab.open", (sessionId?: unknown) =>
      chatTabs.open(typeof sessionId === "string" && sessionId ? sessionId : undefined),
    ),
    chatTabs,
    agentView,
  );
}

// ── Extracted command handlers ──────────────────────────────────

/** ADR-103 VP-2: the console's companion bridge for the agent view, in the
 * first workspace folder, with the Python that imports r4. */
function createCompanionBridge(initialSessionId?: string): CompanionBridge {
  const workspace = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();
  const setting = vscode.workspace.getConfiguration("r4").get<string>("pythonPath", "");
  const { python, tried } = resolveBridgePython({ setting, env: process.env, cliPath: resolveCliPath(), workspace });
  if (!python) {
    throw new Error(`No Python that can import r4 (tried ${tried.join(", ")}). Set r4.pythonPath.`);
  }
  // A resumed tab initializes into its session (the console's tab restore);
  // after a restart (New chat) it opens a fresh one.
  let resume = initialSessionId ?? "";
  return new CompanionBridge({
    python,
    workspace,
    initialSessionId: () => {
      const id = resume;
      resume = "";
      return id;
    },
  });
}

/** ADR-103 VP-0: run `r4 console` in an editor-area terminal (every console
 * function, before the native ones land). */
function openConsoleInEditor(): void {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  const spec = consoleTerminalSpec(resolveCliPath(), folder);
  const terminal = vscode.window.createTerminal({
    ...spec,
    location: vscode.TerminalLocation.Editor,
    iconPath: new vscode.ThemeIcon("terminal"),
  });
  terminal.show();
}

/** Resolve a ready endpoint's model name from an endpoint/job id or a model
 * name string (accepts an optional leading slash, e.g. "/17481"). Returns the
 * model name to activate, or undefined when nothing ready matches. */
async function resolveEndpointModel(
  getClient: () => GatewayClient | undefined,
  idOrModel: string,
): Promise<string | undefined> {
  const key = idOrModel.trim().replace(/^\//, "").toLowerCase();
  if (!key) { return undefined; }
  const c = getClient();
  if (c) {
    try {
      const ready = (await c.listEndpoints()).filter(
        (ep) => ep.status.toLowerCase() === "ready",
      );
      const byId = ready.find((ep) => String(ep.job_id ?? "").toLowerCase() === key);
      if (byId?.model) { return byId.model; }
      const byModel = ready.find((ep) => (ep.model ?? "").toLowerCase() === key);
      if (byModel?.model) { return byModel.model; }
    } catch {
      // Gateway unreachable — fall through to the string heuristic below.
    }
  }
  // Not an endpoint id: a purely numeric key with no match is unusable, but a
  // named key may already be a model name the picker can activate.
  return /^\d+$/.test(key) ? undefined : idOrModel.trim();
}

async function handleSelectEndpoint(
  getClient: () => GatewayClient | undefined,
  statusBar: StatusBar,
): Promise<void> {
  const c = getClient();
  if (!c) {
    vscode.window.showWarningMessage("R4: Not connected. Login first.");
    return;
  }

  try {
    const endpoints = await c.listEndpoints();
    const ready = endpoints.filter(
      (ep) => ep.status.toLowerCase() === "ready",
    );
    if (ready.length === 0) {
      const action = await vscode.window.showInformationMessage(
        "R4: No ready endpoints. Start one?",
        "Provision Endpoint",
      );
      if (action === "Provision Endpoint") {
        void vscode.commands.executeCommand("r4.endpointUp");
      }
      return;
    }

    const picked = await vscode.window.showQuickPick(
      ready.map((ep) => ({
        label: ep.model,
        description: `${ep.backend}${ep.host ? ` · ${ep.host}` : ""}`,
        endpointId: ep.job_id,
      })),
      { title: "Select endpoint for chat" },
    );
    if (!picked) { return; }

    await setDefaultChatModel(picked.label, statusBar);
  } catch (err) {
    showGatewayError(err);
  }
}

async function handleCallTool(
  getClient: () => GatewayClient | undefined,
  item?: MCPToolItem,
): Promise<void> {
  const c = getClient();
  if (!c) {
    vscode.window.showWarningMessage("R4: Not connected. Login first.");
    return;
  }

  const toolName = item?.toolName ?? await vscode.window.showInputBox({
    title: "Call MCP Tool",
    prompt: "Tool name",
  });
  if (!toolName) { return; }

  const argsStr = await vscode.window.showInputBox({
    title: `Arguments for ${toolName}`,
    prompt: "JSON arguments (or leave empty for {})",
    placeHolder: '{"key": "value"}',
  });

  let args: Record<string, unknown> = {};
  if (argsStr) {
    try {
      args = JSON.parse(argsStr);
    } catch {
      vscode.window.showErrorMessage("R4: Invalid JSON arguments.");
      return;
    }
  }

  try {
    const result = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `R4: Calling tool ${toolName}…`,
        cancellable: false,
      },
      () => c.callMCPTool({ tool: toolName, arguments: args }),
    );
    const doc = await vscode.workspace.openTextDocument({
      content: JSON.stringify(result, null, 2),
      language: "json",
    });
    await vscode.window.showTextDocument(doc, { preview: true });
  } catch (err) {
    showGatewayError(err);
  }
}

async function handleTriggerWorkflow(
  getClient: () => GatewayClient | undefined,
  workflowTree: WorkflowTreeProvider,
  item?: WorkflowItem,
): Promise<void> {
  const c = getClient();
  if (!c) {
    vscode.window.showWarningMessage("R4: Not connected. Login first.");
    return;
  }

  const name = item?.workflowName ?? await vscode.window.showInputBox({
    title: "Trigger Workflow",
    prompt: "Workflow name",
  });
  if (!name) { return; }

  const paramsStr = await vscode.window.showInputBox({
    title: `Parameters for ${name}`,
    prompt: "JSON parameters (or leave empty for {})",
    placeHolder: '{"key": "value"}',
  });

  let params: Record<string, unknown> = {};
  if (paramsStr) {
    try {
      params = JSON.parse(paramsStr);
    } catch {
      vscode.window.showErrorMessage("R4: Invalid JSON parameters.");
      return;
    }
  }

  try {
    const execution = await vscode.window.withProgress(
      {
        location: vscode.ProgressLocation.Notification,
        title: `R4: Triggering workflow ${name}…`,
        cancellable: false,
      },
      () => c.triggerWorkflow(name, params),
    );
    vscode.window.showInformationMessage(
      `R4: Workflow triggered — execution ${execution.execution_id}`,
    );
    workflowTree.refresh();
  } catch (err) {
    showGatewayError(err);
  }
}

// ── Helpers ────────────────────────────────────────────────────

async function initClient(auth: AuthManager): Promise<void> {
  const url = await auth.getGatewayUrl();
  const token = await auth.getToken();
  const kind = await auth.getActiveSourceKind();
  // Public endpoints may be unauthenticated (e.g. a local Ollama), so only a
  // URL is required for them.
  if (url && (token || kind === "public")) {
    if (client) {
      client.updateConfig({ baseUrl: url, token: token ?? "", kind });
    } else {
      client = new GatewayClient({ baseUrl: url, token: token ?? "", kind });
    }
    // Reconcile provisioning state: clear stale race-guard keys for
    // endpoints that reached terminal state while extension was closed.
    if (workspaceState) {
      try {
        await reconcileProvisioningState(workspaceState, client);
      } catch {
        // Best-effort — reconciliation failures must not block init
      }
    }
  } else {
    client = undefined;
  }
}

function showGatewayError(err: unknown): void {
  if (err instanceof GatewayError) {
    if (err.status === 401) {
      vscode.window.showErrorMessage("R4: Authentication failed. Run R4: Login to Gateway.");
      return;
    }

    // Try to extract a meaningful message from the backend's JSON error body.
    // Gateway/UMRS errors embed detail, message, or error fields that are
    // more actionable than the HTTP status + path alone.
    let backendMsg = "";
    if (err.body) {
      try {
        const parsed = JSON.parse(err.body);
        backendMsg = parsed.detail ?? parsed.message ?? parsed.error ?? "";
        if (typeof backendMsg !== "string") {
          backendMsg = "";
        }
      } catch {
        // Not JSON — use raw body if short enough
        backendMsg = err.body.length <= 120 ? err.body : "";
      }
    }

    const detail = backendMsg ? ` — ${backendMsg}` : "";
    vscode.window.showErrorMessage(
      `R4: Gateway error ${err.status}${detail}`,
    );
  } else {
    vscode.window.showErrorMessage(
      `R4: ${err instanceof Error ? err.message : String(err)}`,
    );
  }
}

/**
 * Reconcile persisted provisioning state against live endpoints.
 *
 * On extension restart, clear stale ``r4.provisioning.*`` keys for models
 * whose endpoint has reached a truly terminal state (failed/terminated/expired
 * and gone from the registry). **Ready endpoints are NOT terminal** — the key
 * is preserved so re-provision attempts show "already ready".
 *
 * "pending" (submitted but no job_id yet) is always cleared on restart
 * because the job_id was never persisted — the provision may or may not
 * have succeeded, but we can't track it. The user can safely re-provision
 * (the race guard will detect an existing endpoint if one exists).
 */
async function reconcileProvisioningState(
  state: vscode.Memento,
  c: GatewayClient,
): Promise<void> {
  const clearStates = new Set(["failed", "terminated", "expired", "cancelled"]);
  const keys = state
    .keys()
    .filter((k) => k.startsWith("r4.provisioning."));

  if (keys.length === 0) {
    return;
  }

  let endpoints: { job_id: string; status: string }[];
  try {
    const raw = await c.listEndpoints();
    endpoints = raw.map((ep) => ({ job_id: ep.job_id, status: ep.status.toLowerCase() }));
    reconcileOk = true;
  } catch {
    // Can't reconcile now — mark for retry on next endpoint tree refresh
    reconcileOk = false;
    return;
  }

  for (const key of keys) {
    const jobId = state.get<string>(key);
    if (!jobId || jobId === "pending") {
      // "pending" means provision was submitted but job_id not yet known.
      // After restart, this is stale — clear it.
      void state.update(key, undefined);
      continue;
    }
    const ep = endpoints.find((e) => e.job_id === jobId);
    if (!ep || clearStates.has(ep.status)) {
      // Gone or truly terminal (failed/terminated/expired) — clear
      void state.update(key, undefined);
    }
    // Ready, provisioning, starting — keep the key so race guard works
  }
}

function applyTlsConfig(): void {
  const skip = vscode.workspace
    .getConfiguration("r4")
    .get<boolean>("tlsSkipVerify", false);
  if (skip) {
    process.env.NODE_TLS_REJECT_UNAUTHORIZED = "0";
  } else {
    delete process.env.NODE_TLS_REJECT_UNAUTHORIZED;
  }
}

// ── Provision options wizard ───────────────────────────────────

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

interface ProvisionWizardOpts {
  /** Workspace memento used to remember the last-used choices as defaults. */
  state: vscode.Memento;
  /** Whether the caller is an admin (gates the system-tier access level). */
  isAdmin: boolean;
  /** Seed the access level (e.g. from the model scope). */
  defaultTier?: string;
  /** The model's catalog max context length, used to label "Model default". */
  modelMaxLen?: number;
}

const TTL_PRESETS: { label: string; ttl: number }[] = [
  { label: "1 hour", ttl: 3600 },
  { label: "2 hours", ttl: 7200 },
  { label: "4 hours", ttl: 14400 },
  { label: "8 hours", ttl: 28800 },
  { label: "24 hours", ttl: 86400 },
  { label: "No expiry (manual teardown)", ttl: 0 },
];

const CTX_PRESETS = [4096, 8192, 16384, 32768, 65536, 131072];

/**
 * Multi-step QuickPick wizard collecting endpoint provisioning options:
 * access level (tier), TTL, context length, reasoning mode, and Slurm
 * partition. The last-used values are remembered per workspace and offered
 * as defaults. Returns undefined if the user cancels any step.
 */
async function collectProvisionOptions(
  _client: GatewayClient,
  model: string,
  opts: ProvisionWizardOpts,
): Promise<ProvisionOptions | undefined> {
  const { state, isAdmin, defaultTier, modelMaxLen } = opts;

  // ── Step 1: Access level (tier) ──────────────────────────────
  const lastTier = state.get<string>("r4.provision.lastTier");
  const tierChoices: { label: string; tier: "user" | "project" | "system"; detail: string }[] = [
    { label: "User", tier: "user", detail: "Private to you" },
    { label: "Project / group", tier: "project", detail: "Shared with the active project" },
  ];
  if (isAdmin) {
    tierChoices.push({
      label: "System",
      tier: "system",
      detail: "Shared platform-wide (admin only)",
    });
  }
  const seededTier = defaultTier ?? lastTier;
  const tierItems = tierChoices.map((t) => ({
    label: t.tier === seededTier ? `$(check) ${t.label}` : t.label,
    description: t.tier,
    detail: t.detail,
    _tier: t.tier,
  }));
  const tierPick = await vscode.window.showQuickPick(tierItems, {
    title: `Provision ${model} — 1/5: Access level`,
    placeHolder: "Who should be able to use this endpoint?",
  });
  if (!tierPick) { return undefined; }
  const tier = tierPick._tier;

  // ── Step 2: TTL ──────────────────────────────────────────────
  const lastTtl = state.get<number>("r4.provision.lastTtl");
  const ttlItems = [
    ...TTL_PRESETS.map((p) => ({
      label: p.ttl === lastTtl ? `$(check) ${p.label}` : p.label,
      description: p.ttl === 0 ? "ttl=0" : `${p.ttl}s`,
      _ttl: p.ttl,
    })),
    { label: "$(edit) Custom…", description: "Enter minutes", _ttl: -1 },
  ];
  const ttlPick = await vscode.window.showQuickPick(ttlItems, {
    title: `Provision ${model} — 2/5: Time-to-live`,
    placeHolder: "When should the endpoint auto-recycle?",
  });
  if (!ttlPick) { return undefined; }
  let ttlSec = ttlPick._ttl;
  if (ttlSec === -1) {
    const raw = await vscode.window.showInputBox({
      title: `Provision ${model} — TTL (minutes)`,
      prompt: "TTL in minutes (0 = no expiry)",
      value: lastTtl !== undefined ? String(Math.round(lastTtl / 60)) : "120",
      validateInput: (v) =>
        /^\d+$/.test(v.trim()) ? null : "Enter a non-negative whole number of minutes",
    });
    if (raw === undefined) { return undefined; }
    ttlSec = parseInt(raw.trim(), 10) * 60;
  }

  // ── Step 3: Context length ───────────────────────────────────
  const lastCtx = state.get<number>("r4.provision.lastCtx");
  const ctxItems = [
    {
      label: "Model default",
      description: modelMaxLen ? `ctx ${formatCtx(modelMaxLen)}` : "from catalog/profile",
      _ctx: 0,
    },
    ...CTX_PRESETS.map((n) => ({
      label: n === lastCtx ? `$(check) ${formatCtx(n)}` : formatCtx(n),
      description: `${n} tokens`,
      _ctx: n,
    })),
    { label: "$(edit) Custom…", description: "Enter token count", _ctx: -1 },
  ];
  const ctxPick = await vscode.window.showQuickPick(ctxItems, {
    title: `Provision ${model} — 3/5: Context length`,
    placeHolder: "Maximum context window (vLLM --max-model-len)",
  });
  if (!ctxPick) { return undefined; }
  let maxModelLen: number | undefined;
  if (ctxPick._ctx === -1) {
    const raw = await vscode.window.showInputBox({
      title: `Provision ${model} — Context length`,
      prompt: "Max context length in tokens (>= 512)",
      value: lastCtx !== undefined ? String(lastCtx) : "8192",
      validateInput: (v) =>
        /^\d+$/.test(v.trim()) && parseInt(v.trim(), 10) >= 512
          ? null
          : "Enter a whole number >= 512",
    });
    if (raw === undefined) { return undefined; }
    maxModelLen = parseInt(raw.trim(), 10);
  } else if (ctxPick._ctx > 0) {
    maxModelLen = ctxPick._ctx;
  } else {
    maxModelLen = undefined; // model default
  }

  // ── Step 4: Node count ───────────────────────────────────────
  const lastNodes = state.get<number>("r4.provision.lastNodes");
  const nodeItems = [
    { label: lastNodes === 1 ? "$(check) 1 node" : "1 node", description: "Single-node (default)", _nodes: 1 },
    { label: lastNodes === 2 ? "$(check) 2 nodes" : "2 nodes", description: "Two-node pipeline parallel", _nodes: 2 },
    { label: lastNodes === 4 ? "$(check) 4 nodes" : "4 nodes", description: "Four-node (e.g. GLM-5.2 @ 4×4n4gpu-p)", _nodes: 4 },
    { label: lastNodes === 8 ? "$(check) 8 nodes" : "8 nodes", description: "Eight-node large deployment", _nodes: 8 },
    { label: "$(edit) Custom…", description: "Enter number of nodes", _nodes: -1 },
  ];
  const nodePick = await vscode.window.showQuickPick(nodeItems, {
    title: `Provision ${model} — 4/7: Node count`,
    placeHolder: "How many Slurm compute nodes? (>1 for multi-node PP)",
  });
  if (!nodePick) { return undefined; }
  let nodeCount: number | undefined;
  if (nodePick._nodes === -1) {
    const raw = await vscode.window.showInputBox({
      title: `Provision ${model} — Node count`,
      prompt: "Number of Slurm nodes (positive integer)",
      value: lastNodes !== undefined ? String(lastNodes) : "1",
      validateInput: (v) =>
        /^[1-9]\d*$/.test(v.trim()) ? null : "Enter a positive whole number",
    });
    if (raw === undefined) { return undefined; }
    nodeCount = parseInt(raw.trim(), 10);
  } else {
    nodeCount = nodePick._nodes;
  }

  // ── Step 5: GPU count per node ───────────────────────────────
  const lastGpus = state.get<number>("r4.provision.lastGpus");
  const gpuItems = [
    { label: lastGpus === 1 ? "$(check) 1 GPU" : "1 GPU", description: "Single GPU per node (default)", _gpus: 1 },
    { label: lastGpus === 2 ? "$(check) 2 GPUs" : "2 GPUs", description: "Two GPUs per node", _gpus: 2 },
    { label: lastGpus === 4 ? "$(check) 4 GPUs" : "4 GPUs", description: "Four GPUs (e.g. 4n4gpu-p × 4 nodes for GLM-5.2)", _gpus: 4 },
    { label: lastGpus === 8 ? "$(check) 8 GPUs" : "8 GPUs", description: "Eight GPUs per node", _gpus: 8 },
    { label: "$(edit) Custom…", description: "Enter number of GPUs", _gpus: -1 },
  ];
  const gpuPick = await vscode.window.showQuickPick(gpuItems, {
    title: `Provision ${model} — 5/7: GPUs per node`,
    placeHolder: "How many GPUs per compute node?",
  });
  if (!gpuPick) { return undefined; }
  let gpuCount: number | undefined;
  if (gpuPick._gpus === -1) {
    const raw = await vscode.window.showInputBox({
      title: `Provision ${model} — GPUs per node`,
      prompt: "Number of GPUs per node (positive integer)",
      value: lastGpus !== undefined ? String(lastGpus) : "1",
      validateInput: (v) =>
        /^[1-9]\d*$/.test(v.trim()) ? null : "Enter a positive whole number",
    });
    if (raw === undefined) { return undefined; }
    gpuCount = parseInt(raw.trim(), 10);
  } else {
    gpuCount = gpuPick._gpus;
  }

  // ── Step 6: Reasoning ────────────────────────────────────────
  const lastReasoning = state.get<string>("r4.provision.lastReasoning") ?? "auto";
  const reasoningChoices: { label: string; value: "auto" | "on" | "off"; detail: string }[] = [
    { label: "Auto", value: "auto", detail: "Server derives the reasoning parser from the model" },
    { label: "On", value: "on", detail: "Force the model's native thinking/reasoning parser" },
    { label: "Off", value: "off", detail: "Disable reasoning splitting (no <think> stream)" },
  ];
  const reasoningItems = reasoningChoices.map((r) => ({
    label: r.value === lastReasoning ? `$(check) ${r.label}` : r.label,
    description: r.value,
    detail: r.detail,
    _reasoning: r.value,
  }));
  const reasoningPick = await vscode.window.showQuickPick(reasoningItems, {
    title: `Provision ${model} — 6/7: Reasoning`,
    placeHolder: "Reasoning / thinking behaviour for this endpoint",
  });
  if (!reasoningPick) { return undefined; }
  const reasoning = reasoningPick._reasoning;

  // ── Step 7: Partition ────────────────────────────────────────
  const configured = vscode.workspace
    .getConfiguration("r4")
    .get<string[]>("partitions", []);
  const lastPartition = state.get<string>("r4.provision.lastPartition");
  const partitionItems = [
    { label: "Default (site policy)", description: "Let the scheduler choose", _partition: "" },
    ...configured.map((p) => ({
      label: p === lastPartition ? `$(check) ${p}` : p,
      description: "HPC partition",
      _partition: p,
    })),
    { label: "$(edit) Custom…", description: "Enter a partition name", _partition: "\u0000custom" },
  ];
  const partitionPick = await vscode.window.showQuickPick(partitionItems, {
    title: `Provision ${model} — 7/7: Partition`,
    placeHolder: "Slurm partition / HPC queue (optional)",
  });
  if (!partitionPick) { return undefined; }
  let partition: string | undefined;
  if (partitionPick._partition === "\u0000custom") {
    const raw = await vscode.window.showInputBox({
      title: `Provision ${model} — Partition`,
      prompt: "Slurm partition name",
      value: lastPartition ?? "",
      placeHolder: "e.g. gpu, gpu-a100, ai-h200",
    });
    if (raw === undefined) { return undefined; }
    partition = raw.trim() || undefined;
  } else {
    partition = partitionPick._partition || undefined;
  }

  // ── Persist defaults ─────────────────────────────────────────
  await state.update("r4.provision.lastTier", tier);
  await state.update("r4.provision.lastTtl", ttlSec);
  if (maxModelLen !== undefined) {
    await state.update("r4.provision.lastCtx", maxModelLen);
  }
  await state.update("r4.provision.lastReasoning", reasoning);
  if (nodeCount !== undefined) {
    await state.update("r4.provision.lastNodes", nodeCount);
  }
  if (gpuCount !== undefined) {
    await state.update("r4.provision.lastGpus", gpuCount);
  }
  if (partition !== undefined) {
    await state.update("r4.provision.lastPartition", partition);
  }

  const result: ProvisionOptions = { model, tier, ttl_sec: ttlSec, reasoning };
  if (maxModelLen !== undefined) {
    result.max_model_len = maxModelLen;
  }
  if (nodeCount !== undefined && nodeCount > 1) {
    result.node_count = nodeCount;
  }
  if (gpuCount !== undefined && gpuCount > 1) {
    result.gpu_count = gpuCount;
  }
  if (partition !== undefined) {
    result.partition = partition;
  }
  return result;
}
