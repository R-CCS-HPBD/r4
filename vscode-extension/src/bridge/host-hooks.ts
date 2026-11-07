/**
 * The editor hooks every bridge host gets (ADR-103 D-5): the sidebar agent
 * view and each chat tab build their host with these.
 */

import * as os from "node:os";
import * as vscode from "vscode";

import { ensureWorkspaceIndex, workspaceFiles } from "../../../packages/r4-tui-v3/src/lib/mention-scan";
import type { HostHooks } from "./bridge-agent-host";
import { openChangeDiff, openTextTab } from "./editor-hooks";
import { NodeShellRunner } from "./shell-runner";
import { pickModel, pickPrompt, pickRemoteSource, pickVenue } from "./source-picker";
import { pickWebAccess } from "./web-access-picker";

/** The first workspace folder (the bridge's `--fs-root`), else home. */
export function hostWorkspace(): string {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? os.homedir();
}

export function createHostHooks(onPrompt: (text: string) => void): HostHooks {
  const workspace = hostWorkspace();
  ensureWorkspaceIndex(workspace); // warm the `@` index before the first mention
  return {
    onPrompt,
    shell: new NodeShellRunner(workspace),
    openDocument: openTextTab,
    openDiff: openChangeDiff,
    confirm: async (message, action) =>
      (await vscode.window.showWarningMessage(message, { modal: true }, action)) === action,
    workspaceFiles: () => {
      ensureWorkspaceIndex(workspace);
      return workspaceFiles();
    },
    pickWebAccess,
    pickSource: pickRemoteSource,
    pickVenue,
    pickPrompt,
    pickModel,
    openPalette: () => vscode.commands.executeCommand("workbench.action.quickOpen", ">R4: "),
    openTab: (sessionId) => vscode.commands.executeCommand("r4.tab.open", sessionId),
    tabCommand: (args) => vscode.commands.executeCommand("r4.tab.command", args),
  };
}
