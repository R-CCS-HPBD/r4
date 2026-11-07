/**
 * Helpers for syncing the Copilot / R4 native model picker when the user
 * selects an endpoint or model from the sidebar.
 */

import * as vscode from "vscode";
import { R4_LM_VENDOR } from "./lm-provider";
import type { StatusBar } from "./status-bar";

const TRANSIENT_NOTICE_MS = 3500;

/** Brief status-bar notice that clears automatically. */
export function showTransientNotice(message: string): void {
  void vscode.window.setStatusBarMessage(message, TRANSIENT_NOTICE_MS);
}

/**
 * Point the chat model picker at an R4 Platform model.
 * Does not prefill the chat input (no @r4 participant routing).
 */
export async function syncCopilotModelPicker(modelName: string): Promise<boolean> {
  try {
    await vscode.commands.executeCommand(
      "workbench.action.chat.open",
      {
        modelSelector: { vendor: R4_LM_VENDOR, id: modelName },
      },
    );
    return true;
  } catch {
    return false;
  }
}

/** Persist default model, refresh status bar, and sync the chat model picker. */
export async function setDefaultChatModel(
  modelName: string,
  statusBar: StatusBar,
): Promise<void> {
  await vscode.workspace
    .getConfiguration("r4")
    .update("defaultModel", modelName, vscode.ConfigurationTarget.Global);
  statusBar.refresh();

  const synced = await syncCopilotModelPicker(modelName);
  showTransientNotice(
    synced
      ? `$(check) R4: Chat model → ${modelName}`
      : `$(info) R4: Default model → ${modelName}`,
  );
}
