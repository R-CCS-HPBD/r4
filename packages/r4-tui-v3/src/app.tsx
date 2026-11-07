/**
 * R4 Console v3 app root.
 */

import { createEffect, onMount, onCleanup, Show } from "solid-js";
import { useRenderer } from "@opentui/solid";
import { Shell } from "./layout/shell";
import { CommandPaletteOverlay } from "./components/command-palette";
import { SlashPickerOverlay } from "./components/slash-picker";
import { workspace, actions, activeTab } from "./stores";
import { palette, themeEpoch } from "./themes";
import { t, fg, bold } from "@opentui/core";
import { createR4Keymap, updateKeymapClaims, updateKeymapIdle, updateKeymapSpace } from "./keymap";
import { letterShortcutsBlocked } from "./lib/space-shortcuts";
import { createPlatformAdapter } from "./adapters";
import { loadWorkspace, createBackendPoller, stopBackendPoller } from "./hooks/useBackend";
import { createKnowledgePoller, stopKnowledgePoller } from "./hooks/useKnowledge";
import { seedAgentRuntimeDemo } from "./stores/agent-runtime-demo";
import { setDemoMode } from "./lib/console-mode";
import { offlineBannerMessage, resolveDataSource } from "./lib/detect-backend";
import { shutdownCompanionBridge } from "./bridge";
import { ensureApprovalBridgeStarted } from "./bridge/approval-bridge";
import { wireChatTabsOnce } from "./tabs/wiring";
import { installLiveTabWatchers } from "./tabs/live-watchers";
import {
  makeCompletionWatcher,
  shouldSignalCompletion,
  signalCompletion,
} from "./lib/completion-attention";
import { ListOverlay } from "./components/list-overlay";
import { VenuePickerOverlay } from "./components/venue-picker-overlay";
import { RemoteSourcePickerOverlay } from "./components/remote-source-picker-overlay";
import { WebAccessOverlay } from "./components/web-access-overlay";
import { SearchServerOfferOverlay } from "./components/search-server-offer-overlay";
import { StartResearchOverlay } from "./components/start-research-overlay";
import { ConnectWizardOverlay } from "./components/connect-wizard-overlay";
import { StartActionsMenuOverlay } from "./components/start-actions-menu";
import { RewindOverlay } from "./components/rewind-overlay";
import { BtwOverlay } from "./components/btw-overlay";
import { CitationPreviewOverlay } from "./components/citation-preview-overlay";
import { TourWizardOverlay } from "./components/tour-wizard";
import { HelpOverlay } from "./components/help-overlay";
import { TeamDetailOverlay } from "./components/team-detail-overlay";
import { WorkflowExecOverlay } from "./components/workflow-exec-overlay";
import { EndpointDetailOverlay } from "./components/endpoint-detail-overlay";
import { WorkflowDefOverlay } from "./components/workflow-def-overlay";
import { AgentDetailOverlay } from "./components/agent-detail-overlay";
import { umrsWorkflowActions } from "./stores/umrs-workflow";
import { createCompanionChatBackend } from "./adapters/companion-chat";
import { installTerminalKeyInterceptor } from "./terminal/interceptor";
import { termSession } from "./terminal/pty-session";
import { Floating } from "./ui/Floating";

function QuitDialog() {
  return (
    <Floating position="absolute" width="40%" height={5} left="30%" top="40%" flexDirection="column" padding={1}>
      <text fg={palette.statusErr}>Press Ctrl+Q again to quit.</text>
    </Floating>
  );
}

function makeDataSource(forceOffline = false) {
  return resolveDataSource({ forceOffline });
}

export function App(props: {
  forceOffline?: boolean;
  demoVisuals?: boolean;
  /** W2: launch in chat-first frame with the splash branding overlay. */
  chatFirst?: boolean;
  /** A1 (2026-10-05): skip the tab-layout auto-resume (--fresh). */
  fresh?: boolean;
} = {}) {
  const renderer = useRenderer();
  const keymap = createR4Keymap(renderer);
  // Terminal pane: raw-key interceptor for the focused PTY view. Installed
  // AFTER the keymap so prependListener ordering makes it the FIRST global
  // keypress listener (terminal/interceptor.ts documents the focus contract).
  installTerminalKeyInterceptor(renderer);
  // Chat tabs: register the dispatch table + per-bridge attacher before any
  // bridge can spawn (idempotent).
  wireChatTabsOnce();

  // Issue-853-class cleanup (2026-09-28): EVERY exit path destroys the
  // renderer (raw-mode restore) after stopping the bridge.
  // The signal handlers are removed on unmount: every mount added two
  // process listeners that kept that App reachable (2026-10-17).
  let uninstallSignals: (() => void) | null = null;
  let unmounted = false;
  onCleanup(() => {
    unmounted = true;
    uninstallSignals?.();
  });
  void (async () => {
    const { registerExitCleanup, defaultCleanup, safeExit, installExitSignals } = await import("./lib/exit-cleanup");
    const { stopLocalShellsForExit } = await import("./lib/local-shell");
    registerExitCleanup(async () => {
      // The PTY shell is a child of this process — kill it before the
      // renderer restore, never leave an orphaned shell behind. Running
      // `!` commands are detached process groups: stop them too.
      termSession.kill();
      await stopLocalShellsForExit();
      await defaultCleanup(
        () => shutdownCompanionBridge(),
        () => renderer.destroy(),
      );
    });
    if (!unmounted) uninstallSignals = installExitSignals((code) => void safeExit(code));
  })();

  createEffect(() => {
    updateKeymapSpace(keymap, workspace.paneView ?? "overview");
    // Drop letter/chord matches (research A/R/C/v/…, activity t,N) while a
    // `letters` claim holds (typing / overlays).
    updateKeymapIdle(keymap, !letterShortcutsBlocked());
    // Overlay verb bindings (`claim` field) match while their owner claims
    // them (plan H-6: replaces the approvalOpen/teamOverlay/execOverlay fields).
    updateKeymapClaims(keymap);
  });

  // B7: long-turn completion attention — bell + title once the turn ends.
  const completionWatch = makeCompletionWatcher((elapsedMs) => {
    if (shouldSignalCompletion(elapsedMs, workspace.completionBellEnabled)) {
      signalCompletion();
    }
  });
  createEffect(() => {
    completionWatch(activeTab().chatStreaming);
  });

  if (props.chatFirst) {
    actions.setChatFirst(true);
    actions.openSplash();
  }

  onMount(() => {
    const selection = makeDataSource(Boolean(props.forceOffline));
    const { source, offline, reason } = selection;
    // A1 auto-resume (2026-10-05): restore last run's tab layout + resume
    // the active session. Skipped offline/demo and with --fresh.
    if (!offline && !props.fresh) {
      void import("./bridge/tabs-restore").then(({ restoreTabsFromState }) =>
        restoreTabsFromState().catch((err) =>
          actions.sendSystemNotice(
            `session restore skipped: ${err instanceof Error ? err.message : String(err)}`,
          ),
        ),
      );
    }
    installLiveTabWatchers(offline);
    setDemoMode(offline);
    actions.setDemoMode(offline);
    if (offline) {
      if (props.demoVisuals) {
        seedAgentRuntimeDemo();
      }
      // Fixtures simulate connected channels; DEMO MODE banner is the honesty signal.
      actions.setConnection({ gateway: "connected", umrs: "connected", hpc: "connected" });
      const banner = offlineBannerMessage(reason);
      actions.setBackendState({ error: banner });
      if (reason === "r4-unavailable") {
        actions.notify(
          "warning",
          "Offline fallback",
          "r4 CLI was not found — running with demo fixtures. Set R4_TUI_BACKEND=cli after installing r4.",
        );
      } else if (reason === "cli-unavailable") {
        actions.notify(
          "warning",
          "CLI backend unavailable",
          "R4_TUI_BACKEND=cli was set but r4 is not reachable — using demo fixtures.",
        );
      }
    }
    actions.setChatBackend(offline ? source : createCompanionChatBackend());
    actions.setDataSource(source);
    actions.setPlatform(createPlatformAdapter(source));
    void loadWorkspace(source).then(() => {
      if (offline && !props.demoVisuals) {
        actions.setAgents([]);
      }
      if (offline) {
        actions.setBackendState({ error: offlineBannerMessage(reason) });
        // Fixtures simulate connected channels; header DEMO MODE is the honesty signal.
        actions.setConnection({ gateway: "connected", umrs: "connected", hpc: "connected" });
      } else {
        void ensureApprovalBridgeStarted();
        void umrsWorkflowActions.loadCatalog();
      }
      createKnowledgePoller(source, 30_000);
    });
    createBackendPoller(source, 8000);
    onCleanup(() => {
      stopBackendPoller();
      stopKnowledgePoller();
      actions.setPlatform(null);
      void shutdownCompanionBridge();
    });
  });

  return (
    <box width="100%" height="100%" flexDirection="column">
      {/* Keyed on themeEpoch: a /theme switch remounts the whole tree once so
          every non-reactive palette.<key> prop picks up the new colors. The
          SlashPickerOverlay lives INSIDE the remount: keeping it outside made
          the remounted Shell (transcript msgUserBg cells) repaint across the
          absolute-positioned picker — the "theme list overwritten by the user
          message background" bug. Its selection survives remounts via the
          store (workspace.slashPickerSelection). */}
      <Show when={themeEpoch()} keyed>
        {(_epoch: number) => (
          <>
            <Shell />
            <Show when={workspace.commandPaletteOpen}><CommandPaletteOverlay /></Show>
            <Show when={workspace.slashPickerOpen}>
              <SlashPickerOverlay />
            </Show>
            <Show when={workspace.listOverlay != null}>
              <ListOverlay />
            </Show>
            <Show when={workspace.venuePickerOpen}>
              <VenuePickerOverlay />
            </Show>
            <Show when={workspace.remoteSourcePickerOpen}>
              <RemoteSourcePickerOverlay />
            </Show>
            {/* ADR-099 D-2: the agent's web access, asked at session open. */}
            <Show when={workspace.webAccessPrompt}>
              <WebAccessOverlay />
            </Show>
            {/* ADR-102: after /web Public with no search server, offer a local one. */}
            <Show when={workspace.searchServerOffer}>
              <SearchServerOfferOverlay />
            </Show>
            <Show when={workspace.startResearch}>
              <StartResearchOverlay />
            </Show>
            <Show when={workspace.connectWizard}>
              <ConnectWizardOverlay />
            </Show>
            <Show when={workspace.startActionsMenuOpen}>
              <StartActionsMenuOverlay />
            </Show>
            <Show when={workspace.rewindMenuOpen}>
              <RewindOverlay />
            </Show>
            <Show when={workspace.btw.open}>
              <BtwOverlay />
            </Show>
            <Show when={workspace.citationPreview}>
              <CitationPreviewOverlay />
            </Show>
            <Show when={workspace.tourOpen}>
              <TourWizardOverlay />
            </Show>
            <HelpOverlay />
            <TeamDetailOverlay />
            <WorkflowExecOverlay />
            <EndpointDetailOverlay />
            <WorkflowDefOverlay />
            <AgentDetailOverlay />
            <Show when={workspace.quitDialogOpen}><QuitDialog /></Show>
          </>
        )}
      </Show>
      {/* approvals + ask-user moved inline into ChatPanel (V4) — no mounts here */}
    </box>
  );
}

export default App;
