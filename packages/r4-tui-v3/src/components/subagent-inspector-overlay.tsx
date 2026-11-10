/**
 * D-70 S4 child inspector overlay. Opening one never creates a chat tab or
 * loads the parent/child as a writable session; Esc/b returns to the surface
 * underneath (Work-row detail or the session picker).
 */

import { For, Show, createEffect, createMemo, createSignal, onCleanup, onMount } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { actions, workspace } from "../stores";
import type { RpcRail } from "../bridge/rpc";
import {
  createSubagentInspector,
  inspectorRows,
  type InspectorState,
  type InspectorTarget,
} from "../lib/subagent-inspector";
import { useTerminalDims } from "../lib/terminal-dims";
import { wrapProseLine } from "../lib/markdown-wrap";
import { truncate } from "../lib/text";
import { palette } from "../themes";
import { Floating } from "../ui/Floating";
import { KeyTrap } from "./key-trap";

const BODY_ROWS = 22;

export function SubagentInspectorView(props: { target: InspectorTarget; rail?: RpcRail }) {
  const dims = useTerminalDims();
  const controller = createSubagentInspector({
    target: props.target,
    rail: props.rail,
    isActive: () => activeInspector(props.target),
    onChange: (next) => setState(next),
  });
  const [state, setState] = createSignal<InspectorState>(controller.state());
  const [selectedEvent, setSelectedEvent] = createSignal<number | null>(null);
  const [expandedEvent, setExpandedEvent] = createSignal<number | null>(null);
  const [top, setTop] = createSignal(0);

  onMount(() => void controller.start());
  onCleanup(() => controller.stop());
  createEffect(() => {
    if (workspace.activeChatTabId !== props.target.ownerTabId) actions.closeSubagentInspector();
  });
  createEffect(() => {
    const events = state().snapshot?.events ?? [];
    if (!events.some((event) => event.id === selectedEvent())) {
      setSelectedEvent(events[0]?.id ?? null);
      setExpandedEvent(null);
    }
  });

  const lines = createMemo(() => {
    const snapshot = state().snapshot;
    return snapshot ? inspectorRows(snapshot, expandedEvent(), selectedEvent()) : [];
  });
  const wrapped = createMemo(() => {
    const width = Math.max(20, Math.floor(dims().width * 0.88) - 4);
    return lines().flatMap((line) => line ? wrapProseLine(line, width) : [""]);
  });
  createEffect(() => setTop((current) => clampTop(current, wrapped().length)));

  const close = (): void => actions.closeSubagentInspector();
  const moveEvent = (delta: number): void => {
    const events = state().snapshot?.events ?? [];
    const current = events.findIndex((event) => event.id === selectedEvent());
    const next = Math.max(0, Math.min(events.length - 1, current + delta));
    setSelectedEvent(events[next]?.id ?? null);
  };
  const toggleEvent = (): void => {
    const selected = selectedEvent();
    if (selected === null) return;
    setExpandedEvent((current) => current === selected ? null : selected);
  };
  const keys: Record<string, () => unknown> = {
    escape: close,
    b: close,
    q: close,
    up: () => setTop(clampTop(top() - 1, wrapped().length)),
    down: () => setTop(clampTop(top() + 1, wrapped().length)),
    pageup: () => setTop(clampTop(top() - BODY_ROWS + 1, wrapped().length)),
    pagedown: () => setTop(clampTop(top() + BODY_ROWS - 1, wrapped().length)),
    "[": () => moveEvent(-1),
    "]": () => moveEvent(1),
    enter: toggleEvent,
    return: toggleEvent,
    n: () => controller.nextPage(),
    p: () => controller.previousPage(),
    r: () => controller.refresh(),
  };
  const handleKey = (event: KeyEvent): void => {
    const action = keys[event.name];
    if (!action) return;
    event.preventDefault();
    void action();
  };

  return (
    <Floating position="absolute" width="88%" height={BODY_ROWS + 8} left="6%" top="5%" flexDirection="column" padding={1} overflow="hidden">
      <text fg={palette.accent} height={1}>
        {truncate(`subagent · read-only · ${props.target.sessionId}`, Math.max(20, dims().width - 8))}
      </text>
      <Show when={state().error}>
        <text fg={palette.statusErr} height={2}>{state().error}</text>
      </Show>
      <Show when={state().loading}>
        <text fg={palette.fgDim} height={1}>refreshing stored child…</text>
      </Show>
      <box flexDirection="column" height={BODY_ROWS} overflow="hidden">
        <For each={wrapped().slice(top(), top() + BODY_ROWS)}>
          {(line) => <box height={1} overflow="hidden"><text fg={palette.fg}>{line}</text></box>}
        </For>
        <Show when={!state().snapshot && !state().error}>
          <text fg={palette.fgDim}>opening the durable child read-only…</text>
        </Show>
      </box>
      <text fg={palette.fgDim} height={1}>{footer(state(), top(), wrapped().length)}</text>
      <text fg={palette.fgDim} height={1}>
        ↑↓ scroll · [] select event · Enter expand · n/p event pages · r refresh · b/Esc back
      </text>
      <KeyTrap focused captureText onKeyDown={handleKey} />
    </Floating>
  );
}

function activeInspector(target: InspectorTarget): boolean {
  const current = workspace.subagentInspector;
  return current?.sessionId === target.sessionId && workspace.activeChatTabId === target.ownerTabId;
}

function clampTop(top: number, total: number): number {
  return Math.max(0, Math.min(top, Math.max(0, total - BODY_ROWS)));
}

function eventPosition(state: InspectorState): string {
  const snapshot = state.snapshot;
  if (!snapshot) return "events not loaded";
  return `events after ${state.afterId} · ${snapshot.events.length}/${snapshot.total_events}${snapshot.has_more ? " · n next" : ""}${state.canGoBack ? " · p previous" : ""}`;
}

function footer(state: InspectorState, top: number, total: number): string {
  const visible = total > 0 ? `lines ${top + 1}–${Math.min(total, top + BODY_ROWS)}/${total}` : "";
  return `${eventPosition(state)}${visible ? ` · ${visible}` : ""}`;
}

export function SubagentInspectorOverlay() {
  return (
    <Show when={workspace.subagentInspector}>
      {(target) => <SubagentInspectorView target={target()} />}
    </Show>
  );
}
