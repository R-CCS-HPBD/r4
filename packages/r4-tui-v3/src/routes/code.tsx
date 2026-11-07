/**
 * Wave H — code view: git change-set list (A/M/D) + per-file diff + verbs.
 * r arms a revert (R within notice fires), o opens $EDITOR on the file,
 * t runs the repo's tests through the local shell preview path.
 */

import { For, Show } from "solid-js";
import { workspace } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth, paneColumnWidth } from "../lib/term-width";
import { paneBounds } from "../lib/pane-measure";
import { fileDiff } from "../lib/git-changes";
import { DiffBlock } from "../ui/DiffBlock";
import { ParallelDiffBlock, preferSideBySide } from "../ui/ParallelDiffBlock";
import { useTerminalDims } from "../lib/terminal-dims";

const STATUS_FG = { A: palette.diffAdd, M: palette.accent2, D: palette.diffDel, R: palette.diffHunk, "?": palette.fgDim } as const;

/** Pane Panel chrome (bar 1 + padding 2) + this route's padding (2). */
const CODE_PANE_CHROME = 5;

/** Diff column inside the pane: the MEASURED pane width (estimate until
 * the first frame publishes it) minus chrome. Stabilization 2026-10-21
 * (panes-overlays#4): side-by-side was chosen from the TERMINAL width and
 * the diff blocks sized their cells from it too, so in a ~46-col pane the
 * NEW column sat entirely off-screen. */
function codeDiffWidth(termCols: number): number {
  const measured = paneBounds().width;
  const pane = measured > 0 ? measured : paneColumnWidth(termCols, true);
  return Math.max(20, pane - CODE_PANE_CHROME);
}

export function CodeViewRoute() {
  const dims = useTerminalDims();
  const diffWidth = () => codeDiffWidth(dims().width);
  const sideBySide = () => preferSideBySide(diffWidth());
  const selected = () => workspace.changeSets[workspace.changeCursor];
  return (
    <box flexDirection="column" overflow="hidden" flexGrow={1} padding={1}>
      <Show
        when={workspace.changeSets.length > 0}
        fallback={
          <box flexDirection="column">
            <text fg={palette.fgDim}>
              {workspace.testRunSummary ?? "(no git changes in this working tree)"}
            </text>
            <text fg={palette.fgDim} marginTop={1}>
              refresh via /codeview · pane verbs below when files changed
            </text>
          </box>
        }
      >
        <For each={workspace.changeSets}>
          {(change, idx) => {
            const focused = () => idx() === workspace.changeCursor;
            const armed = () => workspace.revertArmPath === change.path;
            return (
              <box height={1} width="100%" overflow="hidden" backgroundColor={focused() ? palette.bgActive : undefined}>
                <text fg={focused() ? palette.accent : STATUS_FG[change.status]}>
                  {truncate(
                    `${armed() ? "⚠ " : change.status}  ${change.path}`,
                    diffWidth(),
                  )}
                </text>
              </box>
            );
          }}
        </For>
      </Show>
      {/* Verb footer — same interaction grammar (Tab in for keys, click also works). */}
      <Show when={selected()}>
        {(change) => (
          <box flexDirection="column" marginTop={1} paddingTop={1} overflow="hidden">
            <box height={1} width="100%" overflow="hidden">
              <text fg={workspace.revertArmPath ? palette.statusWarn : palette.fgDim}>
                {workspace.revertArmPath
                  ? `R = confirm revert of ${truncate(workspace.revertArmPath!, panePanelWidth(1, 14))}  ·  Esc cancels`
                  : "Tab in · ↵ diff · o $EDITOR · r revert · t tests"}
              </text>
            </box>
            {/* Inline preview of the focused file's diff; exact path on top.
                Wide terminals get the side-by-side render (old | new). */}
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.accent2}>
                {`git diff HEAD — ${change().path}${sideBySide() ? " (side-by-side)" : ""}`}
              </text>
            </box>
            <box flexDirection="column" overflow="hidden" maxHeight={18}>
              <Show
                when={sideBySide()}
                fallback={<DiffBlock content={diffPreview(change())} width={diffWidth()} />}
              >
                <ParallelDiffBlock content={diffPreview(change())} width={diffWidth()} />
              </Show>
            </box>
          </box>
        )}
      </Show>
      <Show when={workspace.testRunSummary}>
        {(summary) => (
          <box height={1} width="100%" overflow="hidden" marginTop={1}>
            <text fg={palette.fgDim}>{truncate(summary(), 80)}</text>
          </box>
        )}
      </Show>
    </box>
  );
}

let diffCache: { key: string; text: string } | null = null;
/** Memoize diffs per (path,status) so cursor movement doesn't respawn git. */
function diffPreview(change: { path: string; status: "A" | "M" | "D" | "R" | "?" }): string {
  const key = `${change.status}:${change.path}`;
  if (!diffCache || diffCache.key !== key) {
    diffCache = { key, text: fileDiff(change.path, change.status) };
  }
  return diffCache.text;
}
