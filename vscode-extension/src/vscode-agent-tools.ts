import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import * as vscode from "vscode";
import {
  AgentToolBridge,
  type AgentToolPolicy,
  createCoreToolRegistry,
  createJsonlAuditWriter,
  resolveContainedPath,
} from "./agent-tools";
import {
  applyAnchoredEdits,
  type AnchoredEdit,
} from "./edit-session-core";
import type { EditSessionController } from "./edit-session";

const PROCESS_TIMEOUT_MS = 120_000;
const PROCESS_MAX_BUFFER = 1_000_000;

export async function createVscodeAgentToolBridge(
  context: vscode.ExtensionContext,
  editSession?: EditSessionController,
): Promise<AgentToolBridge | undefined> {
  const workspaceRoot = vscode.workspace.workspaceFolders?.[0]?.uri.fsPath;
  if (!workspaceRoot) return undefined;

  const policyPath = vscode.Uri.joinPath(context.extensionUri, "resources", "agent-tool-policy.json");
  const policy = parsePolicy(JSON.parse(await readFile(policyPath.fsPath, "utf8")));
  const registry = createCoreToolRegistry();
  registry.register("workspace.diagnostics", async (arguments_) => {
    const target = await resolveContainedPath(workspaceRoot, requireString(arguments_, "path"));
    return vscode.languages.getDiagnostics(vscode.Uri.file(target)).map((diagnostic) => ({
      message: diagnostic.message,
      severity: diagnostic.severity,
      source: diagnostic.source,
      code: typeof diagnostic.code === "object" ? diagnostic.code.value : diagnostic.code,
      range: {
        start: diagnostic.range.start,
        end: diagnostic.range.end,
      },
    }));
  });
  registry.register("terminal.run", async (arguments_, context) => {
    const command = requireString(arguments_, "command");
    const args = requireStringArray(arguments_.args, "args");
    const cwd = await resolveContainedPath(workspaceRoot, requireString(arguments_, "cwd"));
    return runProcess(command, args, cwd, context.signal);
  });
  if (editSession) {
    registry.override("workspace.applyEdit", async (arguments_, context) => {
      const target = await resolveContainedPath(workspaceRoot, requireString(arguments_, "path"));
      const relPath = vscode.workspace.asRelativePath(target, false);

      let proposed: string;
      if (typeof arguments_.content === "string") {
        // New-file creation shortcut: treat content as the whole proposal.
        proposed = arguments_.content;
      } else {
        const edits = requireAnchoredEdits(arguments_.edits);
        const base = editSession.store.getForFile(target)?.modifiedText ?? (await readDiskText(target)) ?? "";
        proposed = applyAnchoredEdits(base, edits);
      }
      const edit = await editSession.stageAndReview({
        sessionId: context.sessionId ?? "agent",
        agentCallId: context.toolCallId,
        filePath: target,
        relPath,
        modifiedText: proposed,
      });
      return {
        path: target,
        edit_id: edit.id,
        added: edit.stats.added,
        removed: edit.stats.removed,
        status: "pending_review",
      };
    });
  }

  await vscode.workspace.fs.createDirectory(context.globalStorageUri);
  const auditPath = vscode.Uri.joinPath(context.globalStorageUri, "agent-tool-audit.jsonl").fsPath;
  return new AgentToolBridge(
    workspaceRoot,
    policy,
    registry,
    showApproval,
    createJsonlAuditWriter(auditPath),
  );
}

function requireAnchoredEdits(value: unknown): AnchoredEdit[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error("edits must be a non-empty array of {old_string, new_string}");
  }
  return value.map((raw) => {
    if (typeof raw !== "object" || raw === null) throw new Error("edit must be an object");
    const record = raw as Record<string, unknown>;
    if (typeof record.old_string !== "string" || typeof record.new_string !== "string") {
      throw new Error("edit requires string old_string and new_string");
    }
    return { oldString: record.old_string, newString: record.new_string };
  });
}

async function readDiskText(filePath: string): Promise<string | undefined> {
  try {
    return await vscode.workspace.fs.readFile(vscode.Uri.file(filePath)).then(
      (bytes) => Buffer.from(bytes).toString("utf8"),
      () => undefined,
    );
  } catch {
    return undefined;
  }
}

async function showApproval(request: { tool: string; action: string; scope: string }): Promise<boolean> {
  const choice = await vscode.window.showWarningMessage(
    `${request.action}\nScope: ${request.scope}`,
    { modal: true, detail: `R4 Agent requests ${request.tool}. Approve this action once?` },
    "Allow once",
    "Deny",
  );
  return choice === "Allow once";
}

function parsePolicy(value: unknown): AgentToolPolicy {
  if (!isRecord(value) || typeof value.name !== "string" || !isRecord(value.tools)) {
    throw new Error("R4 Agent tool policy is invalid");
  }
  const tools: AgentToolPolicy["tools"] = {};
  for (const [name, rawRule] of Object.entries(value.tools)) {
    if (!isRecord(rawRule) || !["allow", "approval"].includes(String(rawRule.access))) {
      throw new Error(`R4 Agent tool policy rule is invalid: ${name}`);
    }
    if (rawRule.pathArgument !== undefined && typeof rawRule.pathArgument !== "string") {
      throw new Error(`R4 Agent tool path rule is invalid: ${name}`);
    }
    const commands = parseCommands(rawRule.commands, name);
    tools[name] = {
      access: rawRule.access as "allow" | "approval",
      ...(rawRule.pathArgument ? { pathArgument: rawRule.pathArgument } : {}),
      ...(commands ? { commands } : {}),
    };
  }
  return { name: value.name, tools };
}

function parseCommands(value: unknown, toolName: string): Record<string, string[]> | undefined {
  if (value === undefined) return undefined;
  if (!isRecord(value)) throw new Error(`R4 Agent command policy is invalid: ${toolName}`);
  const commands: Record<string, string[]> = {};
  for (const [command, subcommands] of Object.entries(value)) {
    if (!Array.isArray(subcommands) || !subcommands.every((item) => typeof item === "string")) {
      throw new Error(`R4 Agent command policy is invalid: ${toolName}`);
    }
    commands[command] = subcommands;
  }
  return commands;
}

function runProcess(
  command: string,
  args: string[],
  cwd: string,
  signal?: AbortSignal,
): Promise<{ stdout: string; stderr: string }> {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    let child: ReturnType<typeof execFile>;
    const onAbort = (): void => {
      child.kill();
      reject(new Error("Process cancelled"));
    };
    child = execFile(command, args, {
      cwd,
      shell: false,
      timeout: PROCESS_TIMEOUT_MS,
      maxBuffer: PROCESS_MAX_BUFFER,
      windowsHide: true,
    }, (error, stdout, stderr) => {
      signal?.removeEventListener("abort", onAbort);
      if (error) {
        reject(new Error(`Process failed with exit code ${String(error.code ?? "unknown")}`));
        return;
      }
      resolve({ stdout, stderr });
    });
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

function requireString(arguments_: Record<string, unknown>, name: string): string {
  const value = arguments_[name];
  if (typeof value !== "string" || !value.trim()) throw new Error(`${name} must be a non-empty string`);
  return value;
}

function requireStringArray(value: unknown, name: string): string[] {
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new Error(`${name} must be an array of strings`);
  }
  return value;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
