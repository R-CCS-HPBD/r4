/**
 * B5: structured error card — what failed / what the app did / verb.
 * Extracted from chat.tsx to keep the message renderer readable.
 */

import { createSignal, onMount, Show } from "solid-js";
import { truncate } from "../lib/text";
import { chatContentWidth } from "../lib/term-width";
import { actions } from "../stores";
import { palette } from "../themes";
import { getCompanionBridge } from "../bridge";
import { isOfflineBackend } from "../lib/console-mode";
import type { ErrorCard } from "../types";
import { Panel } from "../ui/Panel";

export function ErrorCardView(props: { msgId: string; card: ErrorCard }) {
  const actionLabel = () =>
    props.card.action === "dropped"
      ? "dropped"
      : props.card.action === "retrying"
        ? "retrying…"
        : "needs you";
  // Wave E: authz denial is an explicit render state — not a generic failure,
  // not empty, not "feature missing".
  const isDenied = () => /denied|forbidden|not authorized|permission/i.test(`${props.card.what} ${props.card.detail ?? ""}`);
  // 2026-09-27: registry-backed remediation hint — the bridge matches a
  // known error id inside the card text (never a guessed id); hidden when
  // nothing matches.
  const [hint, setHint] = createSignal<{ errorId: string; remediation: string } | null>(null);
  onMount(() => {
    if (isOfflineBackend()) return;
    const message = `${props.card.what} ${props.card.detail ?? ""}`.trim();
    if (!message) return;
    void (async () => {
      try {
        const out = (await getCompanionBridge().request(
          "explain.match",
          { message },
          8_000,
        )) as { found?: boolean; error_id?: string; remediation?: string };
        if (out.found && out.error_id && out.remediation) {
          setHint({ errorId: out.error_id, remediation: out.remediation });
        }
      } catch {
        // match is advisory — a lookup failure must never break the card.
      }
    })();
  });
  return (
    <Panel
      flexDirection="column"
      accent={isDenied() ? palette.statusWarn : props.card.action === "dropped" ? palette.statusErr : palette.statusWarn}
      padding={1}
      width="100%"
      overflow="hidden"
      onMouseDown={() => actions.activateErrorCard(props.msgId)}
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={isDenied() ? palette.statusWarn : palette.statusErr}>
          {isDenied() ? "⛔ denied: " : "✗ "}{truncate(props.card.what, chatContentWidth(6))}
        </text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>
          {actionLabel()}
          {props.card.detail ? ` — ${truncate(props.card.detail, chatContentWidth(14))}` : ""}
        </text>
      </box>
      <Show when={hint()}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>
            {`? ${hint()!.errorId}: ${truncate(hint()!.remediation, chatContentWidth(14))} (/explain)`}
          </text>
        </box>
      </Show>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.accent}>{verbLine(props.card)}</text>
      </box>
      <Show when={props.card.alt}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.fgDim}>{truncate(props.card.alt!, chatContentWidth(6))}</text>
        </box>
      </Show>
    </Panel>
  );
}

/** The card's key line: its own verb (click / ↵ on an empty draft) or retry. */
function verbLine(card: ErrorCard): string {
  if (!card.retryable) return "↵ open /logs";
  return `${card.verb ?? "↵ retry"} · l open /logs`;
}
