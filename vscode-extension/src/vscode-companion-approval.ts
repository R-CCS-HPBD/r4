import * as vscode from "vscode";

export type CompanionApprovalDecision = "once" | "always" | "no";

export interface CompanionToolApprovalParams {
  session_id: string;
  request_id: string;
  approval_id: string;
  tool: string;
  description: string;
  arguments?: Record<string, unknown>;
}

export async function showCompanionToolApproval(
  params: CompanionToolApprovalParams,
): Promise<CompanionApprovalDecision> {
  const tool = params.tool || "tool";
  const description = params.description?.trim() || `Call tool: ${tool}`;
  const choice = await vscode.window.showWarningMessage(
    description,
    { modal: true, detail: `R4 Companion requests approval for ${tool}.` },
    "Allow once",
    "Always for session",
    "Deny",
  );
  if (choice === "Allow once") return "once";
  if (choice === "Always for session") return "always";
  return "no";
}

export interface CompanionAskUserParams {
  session_id: string;
  request_id?: string;
  question: string;
  options: string[];
}

const ASK_USER_OTHER = "Other…";

export async function showCompanionAskUser(
  params: CompanionAskUserParams,
): Promise<string> {
  const question = params.question.trim() || "R4 Companion asks:";
  if (params.options.length > 0) {
    const picked = await vscode.window.showQuickPick([...params.options, ASK_USER_OTHER], {
      title: question,
      placeHolder: "Select an answer (Esc to skip)",
      ignoreFocusOut: true,
    });
    if (picked === undefined) return ""; // dismissed → cancelled
    if (picked !== ASK_USER_OTHER) return picked;
  }
  const typed = await vscode.window.showInputBox({
    title: question,
    prompt: "R4 Companion needs more information to proceed.",
    ignoreFocusOut: true,
  });
  return typed?.trim() ?? "";
}
