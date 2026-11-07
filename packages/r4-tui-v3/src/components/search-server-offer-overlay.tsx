/**
 * The offer after /web Public when no search server exists (ADR-102, guided
 * start 2026-11-01): web_search needs a SearXNG; Enter starts a local one
 * (search-server-command.ts startSearchServer, progress as notices), Esc
 * leaves it and says how to start it later. Opened only for a local session
 * with podman or docker installed (the bridge's `search_server_offer`).
 */

import { For } from "solid-js";
import type { KeyEvent } from "@opentui/core";
import { actions, workspace } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { termWidth } from "../lib/term-width";
import { useTerminalDims } from "../lib/terminal-dims";
import { KeyTrap } from "./key-trap";
import { Floating } from "../ui/Floating";

export const OFFER_LINES = [
  "web_search needs a search server, and none is running.",
  "Start a local one now? It runs SearXNG on this machine (127.0.0.1 only)",
  "with podman or docker; the first start downloads about 200 MB.",
];
export const OFFER_HINT = "Enter start · Esc not now";

const cap = () => Math.max(40, Math.floor(termWidth() * 0.64) - 4);

/** Enter: start (the overlay closes first; progress arrives as notices). */
export async function acceptSearchServerOffer(): Promise<void> {
  actions.closeSearchServerOffer();
  const { startSearchServer } = await import("../commands/search-server-command");
  const result = await startSearchServer();
  if (result.kind === "error") actions.sendSystemNotice(`⚠ ${result.message}`);
}

export function SearchServerOfferOverlay() {
  const dims = useTerminalDims();
  const rows = OFFER_LINES.length + 5;
  const boxTop = () => Math.max(0, Math.min(Math.floor(dims().height * 0.25), dims().height - rows));

  const onKey = (event: KeyEvent) => {
    const name = String(event.name ?? "");
    if (name === "return" || name === "enter") {
      event.preventDefault();
      void acceptSearchServerOffer();
    } else if (name === "escape") {
      event.preventDefault();
      actions.declineSearchServerOffer();
    }
  };

  return (
    <Floating
      position="absolute"
      width="64%"
      height={rows}
      left="18%"
      top={boxTop()}
      title=" Web search "
      flexDirection="column"
      padding={1}
    >
      <For each={OFFER_LINES}>
        {(line) => (
          <box height={1} width="100%" overflow="hidden">
            <text fg={palette.fg}>{truncate(line, cap())}</text>
          </box>
        )}
      </For>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>{OFFER_HINT}</text>
      </box>
      <box height={1} width={1} overflow="hidden">
        <KeyTrap focused={workspace.searchServerOffer} onKeyDown={onKey} />
      </box>
    </Floating>
  );
}
