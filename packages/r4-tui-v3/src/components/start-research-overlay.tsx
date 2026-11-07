/**
 * Start research overlay — Create / Import / Plan forms for Mission Control.
 */

import { Show, createMemo, createSignal, createEffect } from "solid-js";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { submitCreate, submitImport, submitPlan } from "../lib/start-research";
import type { KeyEvent } from "@opentui/core";
import { Floating } from "../ui/Floating";

type CreateField = "id" | "name";

export function StartResearchOverlay() {
  const state = () => workspace.startResearch;
  const kind = createMemo(() => state()?.kind);
  const draft = createMemo(() => state()?.draft ?? "");
  const nameDraft = createMemo(() => state()?.nameDraft ?? "");
  const [createField, setCreateField] = createSignal<CreateField>("id");

  createEffect(() => {
    // Reset field focus when opening/changing kind.
    if (state()?.kind === "create") setCreateField("id");
  });

  const title = createMemo(() => {
    switch (kind()) {
      case "create":
        return "New project";
      case "import":
        return "Import sources";
      case "plan":
        return "Research plan goal";
      default:
        return "Start research";
    }
  });

  const hint = createMemo(() => {
    switch (kind()) {
      case "create":
        return "Project id  ·  Tab name  ·  Enter submit  ·  Esc cancel";
      case "import":
        return "Paste absolute paths (space or quoted)  ·  Enter  ·  Esc";
      case "plan":
        return "Research goal text  ·  Enter  ·  Esc";
      default:
        return "";
    }
  });

  const close = () => actions.closeStartResearch();

  const submit = () => {
    const s = state();
    if (!s) return;
    if (s.kind === "create") {
      void submitCreate(s.draft, s.nameDraft);
      return;
    }
    if (s.kind === "import") {
      void submitImport(s.draft);
      return;
    }
    void submitPlan(s.draft);
  };

  const handleKey = (event: KeyEvent) => {
    if (event.name === "escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.name === "tab" && kind() === "create") {
      event.preventDefault();
      setCreateField((f) => (f === "id" ? "name" : "id"));
      return;
    }
    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      submit();
    }
  };

  return (
    <Show when={state()}>
      <Floating
        position="absolute"
        width="60%"
        height={kind() === "create" ? 10 : 8}
        left="20%"
        top="25%"
        flexDirection="column"
        padding={1}
        gap={0}
        overflow="hidden"
      >
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.accent}>{title()}</text>
        </box>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{hint()}</text>
        </box>
        <box
          flexDirection="row"
          height={1}
          width="100%"
          marginTop={1}
          overflow="hidden"
          backgroundColor={palette.msgUserBg}
          paddingLeft={1}
        >
          <text fg={palette.accent}>› </text>
          <input
            value={draft()}
            focused={kind() !== "create" || createField() === "id"}
            onInput={(value: string) => actions.setStartResearchDraft(value)}
            onChange={(value: string) => actions.setStartResearchDraft(value)}
            onKeyDown={handleKey}
            placeholder={
              kind() === "create" ? "project-id" : kind() === "import" ? "/path/to/docs …" : "Investigate…"
            }
            backgroundColor={palette.msgUserBg}
            textColor={palette.fgBright}
            placeholderColor={palette.fg}
            cursorColor={palette.accent}
          />
        </box>
        <Show when={kind() === "create"}>
          <box
            flexDirection="row"
            height={1}
            width="100%"
            marginTop={1}
            overflow="hidden"
            backgroundColor={createField() === "name" ? palette.msgUserBg : undefined}
            paddingLeft={1}
          >
            <text fg={createField() === "name" ? palette.accent : palette.fgDim}>name </text>
            <input
              value={nameDraft()}
              focused={createField() === "name"}
              onInput={(value: string) => actions.setStartResearchNameDraft(value)}
              onChange={(value: string) => actions.setStartResearchNameDraft(value)}
              onKeyDown={handleKey}
              placeholder="optional display name"
              backgroundColor={palette.msgUserBg}
              textColor={palette.fgBright}
              placeholderColor={palette.fg}
              cursorColor={palette.accent}
            />
          </box>
        </Show>
      </Floating>
    </Show>
  );
}
