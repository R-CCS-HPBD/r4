import * as vscode from "vscode";
import type { EditSessionStore, PendingEdit } from "./edit-session-core";

/**
 * Native SCM surface for pending agent edits: review queue in the Source
 * Control sidebar. Clicking an item opens the native diff editor; badges and
 * tooltips carry the line-count stats; title/context menu actions accept or
 * reject single files or the whole queue.
 *
 * Resolved edits leave the queue — final state lives on the transcript cards
 * (and in git itself after write), so this view stays an actionable inbox.
 */
export class EditChangesView implements vscode.Disposable {
  private readonly scm: vscode.SourceControl;
  private readonly group: vscode.SourceControlResourceGroup;
  private readonly decorations: PendingEditDecorations;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly store: EditSessionStore,
    private readonly reopenDiff: (editId: string) => Promise<void>,
  ) {
    this.scm = vscode.scm.createSourceControl("r4-agent-edits", "R4 Agent Edits");
    this.scm.acceptInputCommand = undefined;
    this.group = this.scm.createResourceGroup("pending", "Pending Review");
    this.group.hideWhenEmpty = true;
    this.decorations = new PendingEditDecorations(store);
    this.disposables.push(
      this.scm,
      this.decorations,
      new vscode.Disposable(this.store.onDidChange(() => this.update())),
      vscode.window.registerFileDecorationProvider(this.decorations),
    );
    this.update();
  }

  /** Pending edit for a resource uri, used by context-menu commands. */
  pendingForUri(uri: vscode.Uri): PendingEdit | undefined {
    return this.store.getForFile(uri.fsPath);
  }

  listPending(): PendingEdit[] {
    return this.store.listPending();
  }

  private update(): void {
    const pending = this.store.listPending();
    const added = pending.reduce((sum, edit) => sum + edit.stats.added, 0);
    const removed = pending.reduce((sum, edit) => sum + edit.stats.removed, 0);
    this.group.label = pending.length
      ? `Pending Review — ${pending.length} file${pending.length === 1 ? "" : "s"} (+${added} −${removed})`
      : "Pending Review";
    this.group.resourceStates = pending.map((edit) => toResourceState(edit));
    this.decorations.refresh();
  }

  dispose(): void {
    for (const disposable of this.disposables) disposable.dispose();
  }
}

function toResourceState(edit: PendingEdit): vscode.SourceControlResourceState {
  return {
    resourceUri: vscode.Uri.file(edit.filePath),
    command: {
      command: "r4.edits.openDiff",
      title: "Review agent edit",
      arguments: [edit.id],
    },
    decorations: {
      tooltip: `${edit.relPath} proposed by R4 Agent\n+${edit.stats.added} −${edit.stats.removed} · staged ${edit.stagedAt}`,
    },
    contextValue: "r4Edit.pending",
  };
}

/** Marks files with pending agent edits (badge in Explorer + SCM views). */
class PendingEditDecorations implements vscode.FileDecorationProvider {
  private readonly emitter = new vscode.EventEmitter<vscode.Uri[]>();
  readonly onDidChangeFileDecorations = this.emitter.event;

  constructor(private readonly store: EditSessionStore) {}

  provideFileDecoration(uri: vscode.Uri): vscode.FileDecoration | undefined {
    if (uri.scheme !== "file") return undefined;
    const edit = this.store.getForFile(uri.fsPath);
    if (!edit) return undefined;
    return {
      badge: "±",
      tooltip: `Pending R4 Agent edit (+${edit.stats.added} −${edit.stats.removed})`,
      propagate: false,
    };
  }

  refresh(): void {
    this.emitter.fire([]);
  }

  dispose(): void {
    this.emitter.dispose();
  }
}
