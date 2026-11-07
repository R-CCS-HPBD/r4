/**
 * The editor side of the bridge host (ADR-103 D-5, VP-2b): a changed file
 * opens in VS Code's diff editor, a cited passage in an editor tab.
 */

import * as path from "node:path";
import * as vscode from "vscode";

const SCHEME = "r4-change";
/** Read-only text behind each r4-change: URI, kept for the window's life. */
const contents = new Map<string, string>();
let provider: vscode.Disposable | undefined;

function ensureProvider(): void {
  if (provider) return;
  provider = vscode.workspace.registerTextDocumentContentProvider(SCHEME, {
    provideTextDocumentContent: (uri) => contents.get(uri.toString()) ?? "",
  });
}

function sideUri(file: string, side: "before" | "after", stamp: number): vscode.Uri {
  const clean = file.replace(/^\/+/, "");
  return vscode.Uri.from({ scheme: SCHEME, path: `/${clean}`, query: `${side}-${stamp}` });
}

/** One session change in the diff editor: the file before → after the agent's edits. */
export async function openChangeDiff(file: string, before: string, after: string): Promise<void> {
  ensureProvider();
  const stamp = Date.now();
  const left = sideUri(file, "before", stamp);
  const right = sideUri(file, "after", stamp);
  contents.set(left.toString(), before);
  contents.set(right.toString(), after);
  await vscode.commands.executeCommand("vscode.diff", left, right, `${path.basename(file)} (session change)`);
}

/** Text in a preview editor tab (Markdown). */
export async function openTextTab(_title: string, content: string): Promise<void> {
  const document = await vscode.workspace.openTextDocument({ content, language: "markdown" });
  await vscode.window.showTextDocument(document, { preview: true });
}
