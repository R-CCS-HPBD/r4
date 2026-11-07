/**
 * Endpoint-detail overlay (T5 topic-5, 2026-09-19).
 *
 * Enter on a work/endpoints row: full detail on that registered endpoint
 * (model / status / venue coordinates / lifecycle stamps / cold-start) as a
 * floating card, Esc closes. Honest states follow capability detection —
 * slots appear only when the endpoint actually carries them.
 */

import { For, Show, createMemo } from "solid-js";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { Floating } from "../ui/Floating";
import { endpointLabel, endpointStateText, urlHost } from "../lib/endpoint-label";
import type { UiEndpoint } from "../types";

/** host:port (backend); a configured public source has only its URL (wave 82). */
function venueText(ep: UiEndpoint): string {
  if (ep.isPublic) return urlHost(ep.url);
  const backend = ep.backend && ep.backend !== "unknown" ? ` (${ep.backend})` : "";
  return `${ep.host}:${ep.port}${backend}`;
}

export function EndpointDetailOverlay() {
  const eid = () => workspace.endpointDetailId;
  const endpoint = createMemo(() => {
    const id = eid();
    if (!id) return undefined;
    return workspace.endpoints.find((ep) => ep.id === id);
  });

  const coldStartText = () => {
    const ep = endpoint();
    if (!ep || ep.coldStartMs === undefined) return undefined;
    return `${(ep.coldStartMs / 1000).toFixed(1)}s`;
  };

  const rows = createMemo(() => {
    const ep = endpoint();
    if (!ep) return [];
    const list: string[] = [
      `endpoint  · ${ep.isPublic ? endpointLabel(ep) : ep.servedModelName}`,
      `status    · ${endpointStateText(ep)}`,
      `venue     · ${venueText(ep)}`,
      `tier      · ${ep.tier}${ep.ownerType ? ` · ${ep.ownerType}` : ""}`,
      `url       · ${ep.url}`,
    ];
    if (ep.sourceName) list.push(`source    · ${ep.sourceName}`);
    if (coldStartText()) list.push(`cold-start · ${coldStartText()}`);
    if (ep.createdAt) list.push(`created   · ${ep.createdAt}`);
    if (ep.readyAt) list.push(`ready     · ${ep.readyAt}`);
    if (ep.expiresAt) list.push(`expires   · ${ep.expiresAt}`);
    return list;
  });

  const close = () => actions.setEndpointDetailId(null);

  return (
    <Show when={eid()}>
      <Floating
        position="absolute"
        width="64%"
        left="18%"
        top="12%"
        flexDirection="column"
        padding={2}
      >
        <text fg={palette.accent}>endpoint — Esc closes</text>
        <box height={1} marginTop={1} />
        <For each={rows()}>
          {(row) => (
            <box height={1} overflow="hidden">
              <text fg={palette.fg}>{row}</text>
            </box>
          )}
        </For>
        <box height={1} marginTop={1}>
          <text fg={palette.fgDim}>Esc close</text>
        </box>
      </Floating>
    </Show>
  );
}
