/**
 * Start actions menu — keyboard path for Mission Control (no mouse / WSL).
 * Same interaction as /models picker: Up/Down · Enter · Esc.
 * Press A on Research to open.
 */

import { For, createMemo, createSignal } from "solid-js";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import {
  openImportOrPlan,
  openSwitchPicker,
  runInit,
  runReadiness,
} from "../lib/start-research";
import { handleResearchCancelJob, handleResearchRunDemo, handleResearchRunReal } from "../lib/space-shortcuts";
import { KeyTrap } from "./key-trap";
import type { KeyEvent } from "@opentui/core";
import { Floating } from "../ui/Floating";

const START_ACTIONS = [
  { id: "new", label: "New project" },
  { id: "switch", label: "Switch project" },
  { id: "import", label: "Import paths" },
  { id: "init", label: "Init knowledge" },
  { id: "plan", label: "Plan goal" },
  { id: "ready", label: "Readiness" },
  { id: "run", label: "Run demo (same as R)" },
  { id: "runReal", label: "Run real (--real, needs workflow port)" },
  { id: "cancel", label: "Cancel job (same as C)" },
] as const;

type StartActionId = (typeof START_ACTIONS)[number]["id"];

function runAction(id: StartActionId): void {
  actions.closeStartActionsMenu();
  switch (id) {
    case "new":
      actions.openStartResearch("create");
      return;
    case "switch":
      void openSwitchPicker();
      return;
    case "import":
      openImportOrPlan("import");
      return;
    case "init":
      void runInit();
      return;
    case "plan":
      openImportOrPlan("plan");
      return;
    case "ready":
      void runReadiness();
      return;
    case "run":
      handleResearchRunDemo();
      return;
    case "runReal":
      handleResearchRunReal();
      return;
    case "cancel":
      handleResearchCancelJob();
      return;
  }
}

export function StartActionsMenuOverlay() {
  const [selected, setSelected] = createSignal(0);
  const items = START_ACTIONS;
  const safeSelected = createMemo(() => Math.min(selected(), items.length - 1));

  const close = () => {
    actions.closeStartActionsMenu();
    setSelected(0);
  };

  const handleKey = (event: KeyEvent) => {
    if (event.name === "down" || (event.name === "n" && event.ctrl)) {
      event.preventDefault();
      setSelected((s) => Math.min(s + 1, items.length - 1));
      return;
    }
    if (event.name === "up" || (event.name === "p" && event.ctrl)) {
      event.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
      return;
    }
    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      const item = items[safeSelected()];
      if (item) runAction(item.id);
      return;
    }
    if (event.name === "escape") {
      event.preventDefault();
      close();
    }
  };

  if (!workspace.startActionsMenuOpen) return null;

  return (
    <Floating
      position="absolute"
      width="55%"
      height={16}
      left="22.5%"
      top="20%"
      flexDirection="column"
      padding={1}
      gap={0}
      overflow="hidden"
    >
      <text fg={palette.accent} height={1}>
        Start research ({items.length})
      </text>
      <box
        flexDirection="column"
        width="100%"
        height={items.length}
        overflow="hidden"
        marginTop={1}
        marginBottom={1}
      >
        <For each={[...items]}>
          {(item, idx) => {
            const isSel = () => idx() === safeSelected();
            const line = () => (isSel() ? `▸ ${item.label}` : `  ${item.label}`);
            return (
              <box
                flexDirection="row"
                width="100%"
                height={1}
                backgroundColor={isSel() ? palette.bgHover : undefined}
              >
                <text
                  fg={
                    item.id === "cancel"
                      ? palette.statusErr
                      : isSel()
                        ? palette.fgBright
                        : palette.fg
                  }
                >
                  {line()}
                </text>
              </box>
            );
          }}
        </For>
      </box>
      <text fg={palette.fgDim} height={1}>
        Up/Down · Enter select · Esc cancel
      </text>
      {/* Absolute + captureText: no layout steal, no letter bleed into Context */}
      <KeyTrap focused={true} captureText onKeyDown={handleKey} />
    </Floating>
  );
}
