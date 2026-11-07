/**
 * V4 Detail Pane — the one summoned surface, docked right of chat.
 *
 * Hosts the V3 space routes as *views* (/tasks, /research, /infra …) plus
 * the ActivityConsole as the `logs` view and the interactive TerminalPane
 * as the `term` view. Width comes from the shell (percentage default or the
 * drag-resized column count). The pane publishes its laid-out bounds every
 * frame (`lib/pane-measure.ts`) — drag hit-testing and the terminal's PTY
 * sizing need the REAL columns, never the percentage estimate.
 */

import { ErrorBoundary, createEffect, onCleanup, onMount } from "solid-js";
import { CliRenderEvents } from "@opentui/core";
import { useRenderer } from "@opentui/solid";
import { workspace, setWorkspace } from "../stores";
import { palette } from "../themes";
import { ResearchRoute } from "../routes/research";
import { TasksRoute } from "../routes/tasks";
import { KnowledgeRoute } from "../routes/knowledge";
import { InfrastructureRoute } from "../routes/infrastructure";
import { SettingsRoute } from "../routes/settings";
import { LoopRoute } from "../routes/loop";
import { SessionsRoute } from "../routes/sessions";
import { CodeViewRoute } from "../routes/code";
import { CatalogRoute } from "../routes/catalog";
import { ContextPanel } from "./context-panel";
import { ActivityConsole } from "./activity-console";
import { TerminalPane } from "../components/terminal-pane";
import { publishPaneBounds } from "../lib/pane-measure";
import { paneTitle } from "../lib/pane-title";
import { Panel } from "../ui/Panel";

import { WorkRoute } from "../routes/work";

function routeFor(view: string) {
  switch (view) {
    case "research":
      return <ResearchRoute />;
    case "tasks":
      return <TasksRoute />;
    case "work":
      return <WorkRoute />;
    case "knowledge":
      return <KnowledgeRoute />;
    case "infrastructure":
      return <InfrastructureRoute />;
    case "settings":
      return <SettingsRoute />;
    case "loop":
      return <LoopRoute />;
    case "sessions":
      return <SessionsRoute />;
    case "codeview":
      return <CodeViewRoute />;
    case "catalog":
      return <CatalogRoute />;
    case "term":
      // The interactive PTY shell (terminal-pane.tsx) — not a route under
      // src/routes because it owns a live subprocess.
      return <TerminalPane />;
    case "dashboard":
      // overview: the dashboard-side readouts (no nested chat — the pane
      // would otherwise embed the ChatPanel itself).
      return <ContextPanel />;
    case "logs":
      return <ActivityConsole />;
    default:
      return <ContextPanel />;
  }
}

export function DetailPane(props: { width?: number | `${number}%` } = {}) {
  const view = () => workspace.paneView ?? "overview";
  const renderer = useRenderer();
  let boxEl: { x: number; width: number } | undefined;
  // Publish the laid-out bounds — once after mount and then per frame
  // (drags and terminal resizes change them without remounts).
  onMount(() => {
    const tick = () => {
      if (boxEl) publishPaneBounds(boxEl.x, boxEl.width);
    };
    const frame = () => tick();
    tick();
    // FRAME-starvation guard (quiet console → no frames → stale bounds).
    for (const delay of [0, 40, 120]) setTimeout(tick, delay);
    renderer.on(CliRenderEvents.FRAME, frame);
    onCleanup(() => renderer.off(CliRenderEvents.FRAME, frame));
  });
  const titleText = () => paneTitle(view(), workspace.paneFocus);
  return (
    <Panel
      width={props.width ?? "45%"}
      // The docked pane keeps its designed width (45% / 38%, TUI_REFERENCE
      // §2). A percentage width leaves OpenTUI's default flexShrink=1 (only a
      // numeric width sets 0), so the chat column's wide auto basis squeezed
      // the pane to ~30% (c3-tui D-2); the chat column takes the rest.
      flexShrink={0}
      accent={workspace.paneFocus ? palette.accent : palette.border}
      title={titleText()}
      titleColor={workspace.paneFocus ? palette.accent : palette.accent2}
      tone={workspace.paneFocus ? "raised" : "base"}
      flexDirection="column"
      padding={1}
      overflow="hidden"
      onMouseDown={() => {
        // Click focuses the docked pane (2026-10-06 revision): panes open
        // unfocused so chat stays live; the pointer is how every view —
        // including the Tab-consuming ones — takes the keys. The terminal
        // keeps its own inner click handler (same effect there).
        if (view() !== "term" && !workspace.paneFocus) {
          setWorkspace("paneFocus", true);
        }
      }}
      ref={(el) => (boxEl = el)}
    >
      <ErrorBoundary fallback={(err) => <text fg={palette.statusErr}>Pane error: {err.message}</text>}>
        {routeFor(view())}
      </ErrorBoundary>
    </Panel>
  );
}
