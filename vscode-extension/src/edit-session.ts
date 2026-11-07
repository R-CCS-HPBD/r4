import * as vscode from "vscode";
import { readFile } from "node:fs/promises";
import { EditChangesView } from "./edit-changes-scm";
import {
  EditSessionStore,
  type EditSessionListener,
  type PendingEdit,
  type StageEditInput,
} from "./edit-session-core";

/**
 * vscode shell over {@link EditSessionStore}: owns the `r4-edit-original`
 * content provider, the native `vscode.diff` review surface, and the
 * accept/reject commands. All state lives in the pure core; this file only
 * translates between core events and editor chrome.
 */

export const R4_EDIT_ORIGINAL_SCHEME = "r4-edit-original";

const isMissing = (error: unknown): boolean =>
  error instanceof Error && "code" in error && (error as NodeJS.ErrnoException).code === "ENOENT";

async function readDiskText(filePath: string): Promise<string | undefined> {
  try {
    return await readFile(filePath, "utf8");
  } catch (error) {
    if (isMissing(error)) return undefined;
    throw error;
  }
}

/** Serves pre-edit content on the left side of the native diff editor. */
export class EditSessionContentProvider implements vscode.TextDocumentContentProvider {
  private readonly onDidChangeEmitter = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.onDidChangeEmitter.event;

  constructor(private readonly store: EditSessionStore) {}

  static uriFor(edit: PendingEdit): vscode.Uri {
    return vscode.Uri.from({
      scheme: R4_EDIT_ORIGINAL_SCHEME,
      path: edit.relPath.replace(/^\/+/, ""),
      query: edit.id,
    });
  }

  provideTextDocumentContent(uri: vscode.Uri): string {
    const edit = this.store.get(uri.query);
    if (!edit) return "";
    return edit.originalText ?? "";
  }

  refresh(edit: PendingEdit): void {
    this.onDidChangeEmitter.fire(EditSessionContentProvider.uriFor(edit));
  }
}

export interface EditSessionController {
  readonly store: EditSessionStore;
  readonly provider: EditSessionContentProvider;
  /** Stage a proposal and open the native diff for review. */
  stageAndReview(
    input: Omit<StageEditInput, "originalText"> & { originalText?: string },
  ): Promise<PendingEdit>;
  accept(editId: string): Promise<void>;
  reject(editId: string): Promise<void>;
  /** Re-open the native diff for a pending edit (e.g. from the webview card). */
  reopenDiff(editId: string): Promise<void>;
  /** Discard all pending edits for a chat session (cancel/abort). */
  cancelSession(sessionId: string): Promise<void>;
  onDidChange(listener: EditSessionListener): vscode.Disposable;
  dispose(): void;
}

class EditSessionControllerImpl implements EditSessionController {
  readonly store = new EditSessionStore();
  readonly provider: EditSessionContentProvider;
  private readonly disposables: vscode.Disposable[] = [];

  constructor() {
    this.provider = new EditSessionContentProvider(this.store);
    this.disposables.push(
      vscode.workspace.registerTextDocumentContentProvider(R4_EDIT_ORIGINAL_SCHEME, this.provider),
    );
  }

  onDidChange(listener: EditSessionListener): vscode.Disposable {
    const unsubscribe = this.store.onDidChange(listener);
    return new vscode.Disposable(unsubscribe);
  }

  async stageAndReview(
    input: Omit<StageEditInput, "originalText"> & { originalText?: string },
  ): Promise<PendingEdit> {
    // Compound edits: when a proposal is already staged for this file, diff the
    // new one against the staged text so the review shows only the delta.
    const stagedBase = this.store.getForFile(input.filePath)?.modifiedText;
    const originalText =
      input.originalText !== undefined ? input.originalText : stagedBase ?? (await readDiskText(input.filePath));
    const edit = this.store.stage({ ...input, originalText });
    this.provider.refresh(edit);
    await this.openDiff(edit);
    return edit;
  }

  private async openDiff(edit: PendingEdit): Promise<void> {
    // Right side: an untitled buffer the user never has to save or close.
    // Accept/reject drives everything; the diff itself is read-only review UI.
    const right = vscode.Uri.from({ scheme: "untitled", path: edit.filePath });
    const doc = await vscode.workspace.openTextDocument(right);
    const leftUri = EditSessionContentProvider.uriFor(edit);
    const title = `${edit.relPath} ↔ R4 Agent Edit (+${edit.stats.added} −${edit.stats.removed})`;
    await vscode.window.showTextDocument(doc, { preview: true, preserveFocus: true });
    // Write the proposed content into the untitled buffer (in-memory only).
    const editor = vscode.window.visibleTextEditors.find((e) => e.document.uri.toString() === right.toString());
    if (editor) {
      await editor.edit((builder) => {
        const fullRange = new vscode.Range(
          doc.positionAt(0),
          doc.positionAt(doc.getText().length),
        );
        builder.replace(fullRange, edit.modifiedText);
      });
    }
    await vscode.commands.executeCommand("vscode.diff", leftUri, right, title, { preview: true });
  }

  async accept(editId: string): Promise<void> {
    const edit = this.store.get(editId);
    if (!edit) throw new Error(`Unknown pending edit: ${editId}`);
    await vscode.workspace.fs.writeFile(vscode.Uri.file(edit.filePath), Buffer.from(edit.modifiedText, "utf8"));
    this.store.resolve(editId, "accepted");
    await this.closeDiffFor(edit);
  }

  async reject(editId: string): Promise<void> {
    const edit = this.store.get(editId);
    if (!edit) throw new Error(`Unknown pending edit: ${editId}`);
    this.store.resolve(editId, "rejected");
    await this.closeDiffFor(edit);
  }

  async reopenDiff(editId: string): Promise<void> {
    const edit = this.store.get(editId);
    if (!edit) throw new Error(`Unknown pending edit: ${editId}`);
    await this.openDiff(edit);
  }

  async cancelSession(sessionId: string): Promise<void> {
    const cleared = this.store.clearSession(sessionId);
    for (const edit of cleared) await this.closeDiffFor(edit);
  }

  private async closeDiffFor(edit: PendingEdit): Promise<void> {
    // Close any tab showing this edit's diff or untitled proposal buffer.
    const original = EditSessionContentProvider.uriFor(edit).toString();
    for (const group of vscode.window.tabGroups.all) {
      for (const tab of group.tabs) {
        const input = tab.input;
        const uris: string[] = [];
        if (input instanceof vscode.TabInputText) uris.push(input.uri.toString());
        if (input instanceof vscode.TabInputTextDiff) {
          uris.push(input.original.toString(), input.modified.toString());
        }
        if (uris.includes(original)) await vscode.window.tabGroups.close(tab, true);
      }
    }
  }

  dispose(): void {
    for (const disposable of this.disposables) disposable.dispose();
  }
}

/** Create controller + review commands + the SCM review queue. */
export function createEditSessionController(context: vscode.ExtensionContext): EditSessionController {
  const controller = new EditSessionControllerImpl();
  const changes = new EditChangesView(controller.store, (editId) => controller.reopenDiff(editId));
  const forResource = (state?: vscode.SourceControlResourceState) =>
    state ? changes.pendingForUri(state.resourceUri) : undefined;
  context.subscriptions.push(
    controller,
    changes,
    vscode.commands.registerCommand("r4.edits.accept", async (editId: string) => {
      if (typeof editId !== "string") return;
      await controller.accept(editId);
    }),
    vscode.commands.registerCommand("r4.edits.reject", async (editId: string) => {
      if (typeof editId !== "string") return;
      await controller.reject(editId);
    }),
    vscode.commands.registerCommand("r4.edits.openDiff", async (editId: string) => {
      if (typeof editId !== "string") return;
      await controller.reopenDiff(editId);
    }),
    vscode.commands.registerCommand(
      "r4.edits.acceptUri",
      async (state?: vscode.SourceControlResourceState) => {
        const edit = forResource(state);
        if (edit) await controller.accept(edit.id);
      },
    ),
    vscode.commands.registerCommand(
      "r4.edits.rejectUri",
      async (state?: vscode.SourceControlResourceState) => {
        const edit = forResource(state);
        if (edit) await controller.reject(edit.id);
      },
    ),
    vscode.commands.registerCommand("r4.edits.acceptAll", async () => {
      for (const edit of changes.listPending()) await controller.accept(edit.id);
    }),
    vscode.commands.registerCommand("r4.edits.rejectAll", async () => {
      for (const edit of changes.listPending()) await controller.reject(edit.id);
    }),
  );
  return controller;
}
