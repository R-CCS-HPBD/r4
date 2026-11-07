/**
 * Tasks route — plan-task monitor with ask / Research / Knowledge handoffs.
 */

import { For, Show, createEffect, createMemo, createSignal, onMount } from "solid-js";
import { workspace, actions } from "../stores";
import { palette, statusColor, statusIcon } from "../themes";
import { truncate } from "../lib/text";
import { clampIndex, handleListKey } from "../lib/selectable-list";
import { TASKS_EMPTY, TASKS_HOW_TO } from "../lib/empty-hints";
import { handleTaskShortcut } from "../lib/space-shortcuts";
import { KeyTrap } from "../components/key-trap";
import type { KeyEvent } from "@opentui/core";

export function TasksRoute() {
  const tasks = createMemo(() => workspace.tasks);
  const [selected, setSelected] = createSignal(0);
  const [listFocused, setListFocused] = createSignal(true);

  const syncSelection = (index: number) => {
    const list = tasks();
    const i = clampIndex(index, list.length);
    setSelected(i);
    const task = list[i];
    if (task) actions.selectObject(task.id);
  };

  onMount(() => {
    if (tasks().length > 0) {
      const existing = workspace.selectedObjectId;
      const idx = existing ? tasks().findIndex((t) => t.id === existing) : 0;
      syncSelection(idx >= 0 ? idx : 0);
    }
  });

  createEffect(() => {
    const list = tasks();
    if (list.length === 0) return;
    if (!list.some((t) => t.id === workspace.selectedObjectId)) {
      syncSelection(0);
    }
  });

  const handleKey = (event: KeyEvent) => {
    const name = event.name;
    if (name === "a" || name === "r" || name === "k") {
      if (handleTaskShortcut(name)) {
        event.preventDefault();
        return;
      }
    }
    if (
      handleListKey(name, {
        selected: selected(),
        length: tasks().length,
        ctrl: Boolean(event.ctrl),
        onMove: (i) => {
          event.preventDefault();
          syncSelection(i);
        },
        onEnter: (i) => {
          event.preventDefault();
          syncSelection(i);
          handleTaskShortcut("enter");
        },
      })
    ) {
      /* handled */
    }
  };

  return (
    <box flexDirection="column" gap={0} width="100%" height="100%" overflow="hidden">
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgBright}>Tasks</text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>{TASKS_HOW_TO}</text>
      </box>
      <Show
        when={tasks().length > 0}
        fallback={
          <box height={1} width="100%" overflow="hidden" marginTop={1}>
            <text fg={palette.fgDim}>{TASKS_EMPTY}</text>
          </box>
        }
      >
        <box flexDirection="column" flexGrow={1} overflow="hidden" marginTop={1}>
          <For each={tasks()}>
            {(task, idx) => {
              const isSel = () => idx() === selected();
              const line = () =>
                truncate(
                  `${isSel() ? "▸" : " "} ${statusIcon(task.status)} ${task.title}`,
                  72,
                );
              return (
                <box
                  height={1}
                  width="100%"
                  overflow="hidden"
                  backgroundColor={isSel() ? palette.bgActive : undefined}
                  onMouseDown={() => {
                    setListFocused(true);
                    syncSelection(idx());
                  }}
                >
                  <text fg={isSel() ? palette.fgBright : statusColor(task.status)}>{line()}</text>
                </box>
              );
            }}
          </For>
        </box>
        <KeyTrap paneScoped focused={listFocused()} onKeyDown={handleKey} />
      </Show>
    </box>
  );
}
