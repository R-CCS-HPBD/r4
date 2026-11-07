/**
 * StateWrapper — honest state container for TUI panels.
 *
 * Renders one of six states depending on the panel's data and health:
 *   empty  – no data yet
 *   loading – fetching from the platform
 *   live   – data present and fresh
 *   stale  – data present but possibly out of date
 *   error  – data fetch failed
 *   degraded – partial data (degraded mode)
 *
 * Synthetic/demo data is gated behind the `--demo` CLI flag.
 * Without it, the wrapper always renders the honest state.
 */

import { Show, createMemo, type JSX } from "solid-js";
import { bold, dim, t } from "@opentui/core";
import { palette } from "../themes";
import { Panel } from "../ui/Panel";
import { StyledTextView } from "../ui/StyledTextView";

/** Honest state identifiers. */
export type StateKind = "empty" | "loading" | "live" | "stale" | "error" | "degraded";

/** Props for the StateWrapper component. */
export interface StateWrapperProps {
  /** The current panel state. */
  state: StateKind;
  /** Title/header shown for the panel. */
  title?: string;
  /** Loading progress (0.0–1.0). */
  progress?: number;
  /** Error message (when state === "error"). */
  errorMessage?: string;
  /** Staleness reason (when state === "stale"). */
  staleReason?: string;
  /** Content to render when state === "live" or "degraded". */
  children?: JSX.Element;
  /** Optional subtitle (always shown when present). */
  subtitle?: string;
  /** Message shown in empty state (alias for subtitle). */
  emptyMessage?: string;
}

/** State colors by kind. */
const STATE_COLORS: Record<StateKind, { border: string; label: string }> = {
  empty: { border: palette.fgDim, label: "EMPTY" },
  loading: { border: palette.accent, label: "LOADING" },
  live: { border: palette.statusOk, label: "LIVE" },
  stale: { border: palette.statusWarn, label: "STALE" },
  error: { border: palette.statusErr, label: "ERROR" },
  degraded: { border: palette.statusWarn, label: "DEGRADED" },
};

/**
 * StateWrapper component.
 *
 * Wraps panel content with an honest state banner and conditional rendering.
 */
export function StateWrapper(props: StateWrapperProps) {
  const stateInfo = createMemo(() => STATE_COLORS[props.state] ?? STATE_COLORS.empty);
  const emptyText = () => props.emptyMessage ?? props.subtitle ?? "No data available.";

  return (
    <Panel
      accent={stateInfo().border}
      tone="raised"
      flexDirection="column"
      padding={1}
      gap={0}
      width="100%"
      height="100%"
    >
      <box flexDirection="row" justifyContent="space-between" height={1}>
        <StyledTextView fg={stateInfo().border} text={t`${bold(props.title ?? "Panel")}`} />
        <StyledTextView fg={palette.fgDim} text={t`${dim(stateInfo().label)}`} />
      </box>

      <Show when={props.state === "stale" && props.staleReason}>
        <text fg={palette.statusWarn}>{props.staleReason}</text>
      </Show>
      <Show when={props.state === "error" && props.errorMessage}>
        <text fg={palette.statusErr}>Error: {props.errorMessage}</text>
      </Show>

      <Show when={props.state === "loading"}>
        <box flexDirection="row" paddingY={1}>
          <text fg={palette.accent}>⠋ Fetching...</text>
          <Show when={props.progress != null}>
            <text fg={palette.fgDim}> ({Math.round((props.progress ?? 0) * 100)}%)</text>
          </Show>
        </box>
      </Show>

      <Show when={props.state === "live" || props.state === "degraded"}>
        <box flexDirection="column" width="100%" height="100%">
          {props.children}
        </box>
      </Show>
      <Show when={props.state === "empty"}>
        <text fg={palette.fgDim}>{emptyText()}</text>
      </Show>

      <Show when={props.state === "degraded"}>
        <box flexDirection="row" paddingY={1} justifyContent="flex-end">
          <StyledTextView fg={palette.statusWarn} text={t`${dim("⚠ Partial data — some sources unavailable")}`} />
        </box>
      </Show>
    </Panel>
  );
}

/**
 * Default empty content — used when a panel has no data.
 */
export function EmptyPanel(props: { subtitle?: string }) {
  return (
    <text fg={palette.fgDim}>{props.subtitle ?? "No data available."}</text>
  );
}
