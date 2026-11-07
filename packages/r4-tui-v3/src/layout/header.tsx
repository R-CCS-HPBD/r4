/**
 * Header with mascot, banner, and connection status.
 */

import { For, Show } from "solid-js";
import { workspace, activeTab } from "../stores";
import { palette, statusColor, icons } from "../themes";
import { Mascot } from "../components";
import { isDemoVisuals } from "../lib/console-mode";
import { t, fg, bold, dim } from "@opentui/core";
import { Panel } from "../ui/Panel";

export function Header() {
  const projectName = () => activeTab().project?.name ?? "(no project)";
  const user = process.env["USER"] ?? "researcher";
  const conn = () => workspace.connection;
  const demo = () => workspace.demoMode;

  const channels = () =>
    [
      { icon: icons.endpoint, label: "gw", state: conn().gateway },
      { icon: icons.job, label: "umrs", state: conn().umrs },
      { icon: icons.agent, label: "hpc", state: conn().hpc },
    ] as const;

  const statusLine = () => {
    if (demo()) return null;
    const approval = activeTab().toolApproval;
    if (approval) {
      return `awaiting approval: ${approval.tool}`;
    }
    if (activeTab().askUser) {
      return "awaiting your answer (ask_user)";
    }
    if (activeTab().chatTurnStatus) return activeTab().chatTurnStatus;
    if (workspace.backendLoading && !activeTab().project) return "↻ loading backend…";
    if (activeTab().chatFallbackActive) {
      return `chat fallback${activeTab().chatFallbackDetail ? ` · ${activeTab().chatFallbackDetail}` : ""}`;
    }
    if (workspace.backendError) return workspace.backendError;
    return "backend ready";
  };

  return (
    <Panel
      width="100%"
      height={7}
      accent={demo() ? palette.statusWarn : palette.border}
      flexDirection="row"
      justifyContent="space-between"
      alignItems="center"
      paddingLeft={1}
      paddingRight={1}
    >
      <box flexDirection="row" alignItems="center" gap={2}>
        <Show when={isDemoVisuals()}>
          <Mascot />
        </Show>
        <box flexDirection="column">
          <text children={t`${bold(fg(palette.accent)("R4 Console v3"))}`} />
          <Show when={demo()}>
            <text children={t`${bold(fg(palette.statusWarn)("DEMO MODE"))} ${dim("— fixtures only")}`} />
          </Show>
          <text children={t`${fg(palette.fgBright)(projectName())}`} />
          <text children={t`${dim("user:")} ${fg(palette.fgBright)(user)}`} />
        </box>
      </box>
      <box flexDirection="column" alignItems="flex-end" width="45%" gap={0}>
        <box flexDirection="row" gap={2} flexWrap="wrap" justifyContent="flex-end">
          <For each={channels()}>
            {(ch) => (
              <text children={t`${ch.icon} ${dim(ch.label)} ${fg(statusColor(ch.state))(ch.state)}`} />
            )}
          </For>
        </box>
        <Show when={statusLine()}>
          <text
            fg={
              activeTab().toolApproval || activeTab().askUser || activeTab().chatTurnStatus
                ? palette.statusWarn
                : activeTab().chatFallbackActive
                  ? palette.statusWarn
                  : palette.fgDim
            }
          >
            {statusLine()!}
          </text>
        </Show>
      </box>
    </Panel>
  );
}
