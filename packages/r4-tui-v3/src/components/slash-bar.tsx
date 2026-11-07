/**
 * SlashBar — inline command input backed by the unified slash catalog.
 *
 * Enter / Tab contract (tui-redesign FR-1):
 * - Enter on no-arg command → dispatch once
 * - Enter on arg command without args → insert `name ` into chat (no dispatch)
 * - Tab → insert-only (never dispatch)
 *
 * When the bar is open it owns slash dispatch; chat must not also dispatch.
 */

import { For, Show, createEffect, createMemo, createSignal, onMount } from "solid-js";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import type { KeyEvent } from "@opentui/core";
import {
  argHintLineForInput,
  filterSlashCommands,
  loadSlashCatalog,
  needsArgPrompt,
  type SlashCommandEntry,
  type SlashDispatchResult,
} from "../commands/slash";
import { Floating } from "../ui/Floating";

/** Open slash bar only when the user newly starts typing `/`, not while editing. */
export function shouldOpenSlashBarFromChatInput(prev: string, next: string): boolean {
  return !prev.startsWith("/") && next.startsWith("/");
}

export interface SlashBarProps {
  /** Optional override catalog (tests). When omitted, loads the unified catalog. */
  commands?: SlashCommandEntry[];
  /** Max suggestion rows. Defaults to 8. */
  maxSuggestions?: number;
  /** Optional submit hook for tests. Defaults to actions.dispatchSlashFromChat. */
  onDispatch?: (line: string) => Promise<SlashDispatchResult> | SlashDispatchResult;
}

const SLASH_NAME_COL = 24;
const SLASH_HELP_MAX = 48;

/** One terminal row: marker, padded command name, truncated help. */
export function formatSlashSuggestionLine(
  cmd: Pick<SlashCommandEntry, "name" | "help">,
  selected: boolean,
): string {
  const marker = selected ? "▸" : " ";
  const name = `/${cmd.name}`;
  const pad = Math.max(1, SLASH_NAME_COL - name.length);
  const help = (cmd.help ?? "").replace(/\s+/g, " ").trim();
  const clipped =
    help.length > SLASH_HELP_MAX ? `${help.slice(0, SLASH_HELP_MAX - 1)}…` : help;
  return `${marker} ${name}${" ".repeat(pad)}${clipped}`;
}

export function resolveSlashBarLine(
  raw: string,
  chosen?: Pick<SlashCommandEntry, "name">,
): string {
  const normalized = raw.trim().startsWith("/") ? raw.trim() : `/${raw.trim()}`;
  if (!chosen) return normalized;

  const body = normalized.slice(1).trim();
  const parts = body.split(/\s+/).filter(Boolean);
  const chosenName = chosen.name;

  // Keep explicit multi-token paths (e.g. /endpoint up must not collapse to /up).
  if (parts.length > 1 && !chosenName.includes(" ")) {
    return normalized;
  }

  const chosenLine = `/${chosenName}`;
  if (body !== chosenName && !body.startsWith(`${chosenName} `)) {
    return chosenLine;
  }
  return normalized;
}

export function SlashBar(props: SlashBarProps) {
  const maxSug = props.maxSuggestions ?? 8;

  const [catalog, setCatalog] = createSignal<SlashCommandEntry[]>(props.commands ?? []);
  const [input, setInput] = createSignal("");
  const [selectedIndex, setSelectedIndex] = createSignal(0);

  onMount(() => {
    const seed = workspace.slashBarSeed;
    setInput(seed ? `/${seed}` : "/");
    if (!props.commands) {
      void loadSlashCatalog().then((entries) => setCatalog(entries));
    }
  });

  createEffect(() => {
    if (props.commands) setCatalog(props.commands);
  });

  const matches = createMemo(() => {
    const raw = input().trim();
    if (!raw.startsWith("/")) return [] as SlashCommandEntry[];
    const needle = raw.slice(1);
    const list = filterSlashCommands(catalog(), needle);
    return list.slice(0, maxSug);
  });

  const safeIndex = createMemo(() => {
    const max = Math.max(0, matches().length - 1);
    return Math.min(selectedIndex(), max);
  });

  // Usage hint while args are typed (e.g. /acquisition extend <graph>) —
  // without it a takesArgs command is a blank wall once selected.
  const argHintLine = createMemo(() =>
    argHintLineForInput(catalog(), input(), matches().length),
  );

  const panelHeight = createMemo(() => {
    const n = matches().length;
    // input + suggestions + [hint] + footer (+ container padding)
    const hint = argHintLine() ? 1 : 0;
    const rows = n > 0 ? 1 + n + 1 : 1 + 1;
    return Math.min(Math.max(rows + hint + 2, 4), 22);
  });

  const handleInput = (value: string) => {
    setInput(value.startsWith("/") || value === "" ? value : `/${value}`);
    setSelectedIndex(0);
  };

  const insertIntoChat = (text: string) => {
    actions.setPendingChatDraft(text);
    setInput("");
    setSelectedIndex(0);
    actions.closeSlashBar();
  };

  const runDispatch = async (line: string) => {
    const dispatch =
      props.onDispatch ??
      (async (cmd: string) => actions.dispatchSlashFromChat(cmd));
    try {
      const result = await dispatch(line);
      if (result.kind === "insert") {
        insertIntoChat(result.text);
        return;
      }
      // Errors are surfaced by dispatchSlashFromChat — do not duplicate notices here.
    } catch (err) {
      actions.sendSystemNotice(err instanceof Error ? err.message : String(err));
    }
    setInput("");
    setSelectedIndex(0);
    actions.closeSlashBar();
  };

  const handleKey = (event: KeyEvent) => {
    const m = matches();

    if (event.name === "return" || event.name === "enter") {
      event.preventDefault();
      const raw = input().trim();
      if (!raw) return;

      // Prefer selected suggestion when present
      const chosen = m.length > 0 ? m[safeIndex()] : undefined;
      const line = resolveSlashBarLine(raw, chosen);

      // Peek takesArgs from chosen or catalog match
      const entry =
        chosen ??
        catalog().find((c) => {
          const name = line.slice(1).split(/\s+/)[0]?.toLowerCase();
          return c.name === name || (c.aliases ?? []).includes(name ?? "");
        });

      if (entry && needsArgPrompt(entry, line.includes(" ") ? line.slice(line.indexOf(" ") + 1) : "")) {
        insertIntoChat(`/${entry.name} `);
        return;
      }

      void runDispatch(line);
      return;
    }

    if (event.name === "escape") {
      event.preventDefault();
      const remainder = input().trim();
      setInput("");
      setSelectedIndex(0);
      actions.closeSlashBar();
      if (remainder && remainder !== "/") {
        actions.setPendingChatDraft(remainder);
      }
      return;
    }

    if (event.name === "tab" || event.name === "shift+tab") {
      event.preventDefault();
      if (event.name === "tab" && m.length > 0) {
        const chosen = m[safeIndex()];
        if (chosen) {
          // Tab = insert-only into chat, never dispatch
          insertIntoChat(`/${chosen.name}${chosen.takesArgs ? " " : ""}`);
        }
      }
      return;
    }

    if (event.name === "up") {
      event.preventDefault();
      setSelectedIndex((i) => Math.max(0, i - 1));
      return;
    }

    if (event.name === "down") {
      event.preventDefault();
      setSelectedIndex((i) => Math.min(Math.max(0, m.length - 1), i + 1));
      return;
    }

    if (event.name === "d" && event.ctrl) {
      event.preventDefault();
      actions.closeSlashBar();
    }
  };

  if (!workspace.slashBarOpen) return null;

  return (
    <Floating
      position="absolute"
      width="55%"
      minHeight={4}
      maxHeight={22}
      height={panelHeight()}
      left="22.5%"
      // Sit above chat / below Actions so the command list stays readable.
      top="42%"
      flexDirection="column"
      padding={1}
      gap={0}
      overflow="hidden"
    >
      <box flexDirection="row" gap={1} alignItems="center" height={1} marginBottom={1}>
        <text fg={palette.accent}>›</text>
        <input
          placeholder="/help"
          value={input()}
          width="100%"
          focused={true}
          onInput={handleInput}
          onKeyDown={handleKey}
          backgroundColor={palette.msgUserBg}
          textColor={palette.fgBright}
          placeholderColor={palette.fg}
          cursorColor={palette.accent}
        />
      </box>

      <Show when={matches().length > 0}>
        <box flexDirection="column" width="100%" overflow="hidden" flexGrow={1}>
          <For each={matches()}>
            {(cmd, idx) => {
              const isSel = () => idx() === safeIndex();
              return (
                <box
                  flexDirection="row"
                  width="100%"
                  height={1}
                  backgroundColor={isSel() ? palette.bgActive : undefined}
                >
                  <text fg={isSel() ? palette.fgBright : palette.fg}>
                    {formatSlashSuggestionLine(cmd, isSel())}
                  </text>
                </box>
              );
            }}
          </For>
        </box>
      </Show>

      <Show when={argHintLine()}>
        {(line) => (
          <text fg={palette.accent2} height={1} width="100%" overflow="hidden">
            {line()}
          </text>
        )}
      </Show>

      <text fg={palette.fgDim} height={1}>
        {matches().length > 0
          ? "Up/Down · Tab insert · Enter submit · Esc close"
          : "Type /command · Tab insert · Enter submit · Esc close"}
      </text>
    </Floating>
  );
}

/** @deprecated Use SlashCommandEntry from commands/slash — kept for type re-exports. */
export type SlashCommand = {
  id: string;
  label: string;
  description?: string;
  icon?: string;
  slash: string;
};
