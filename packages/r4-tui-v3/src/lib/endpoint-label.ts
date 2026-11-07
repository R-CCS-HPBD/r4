/**
 * Display text for one endpoint-feed row (Infrastructure, Work, the endpoint
 * detail card, the console events).
 *
 * A configured PUBLIC source is listed by `r4 endpoint list --all` as one row
 * (`instance_id: "public"`, `status: "configured"`, `model: null`): it is not
 * a provisioned endpoint and names no model, so every surface printed the
 * mapper's "unknown" for it — once per public source (maintainer report
 * 2026-10-25, wave 82). Its label is the source, the word public and the
 * URL host; its state is "configured"; its models come from `r4 model list`
 * (ModelsPanel), not from this row.
 */

import type { UiEndpoint } from "../types";

type LabelledEndpoint = Pick<UiEndpoint, "model" | "servedModelName" | "isPublic" | "sourceName" | "url">;

/** The state word a public-source row shows instead of its mapped status. */
export const PUBLIC_ENDPOINT_STATE = "configured";

/** Neutral marker for a configured public source (not ● ready / ○ stopped). */
export const PUBLIC_ENDPOINT_GLYPH = "◇";

/** Host part of an endpoint URL; the raw text when it does not parse. */
export function urlHost(url: string): string {
  try {
    return new URL(url).host || url;
  } catch {
    return url;
  }
}

function knownName(value: string | undefined): string | undefined {
  return value && value !== "unknown" ? value : undefined;
}

function hostOf(ep: Pick<UiEndpoint, "url">): string {
  return ep.url ? urlHost(ep.url) : "";
}

/** The name a public-source row is filed under: its source, or — a legacy
 * `endpoint_url` row carries no `_source` — its URL host, never "unknown". */
export function publicSourceName(ep: Pick<UiEndpoint, "sourceName" | "url">): string {
  return ep.sourceName || hostOf(ep) || "public";
}

/** `<source> · public · <host>` for a public-source row; `public · <host>`
 * for a legacy row with no source. */
export function publicEndpointLabel(ep: LabelledEndpoint): string {
  const parts = ep.sourceName ? [ep.sourceName, "public"] : ["public"];
  const host = hostOf(ep);
  if (host) parts.push(host);
  return parts.join(" · ");
}

/** The model a row serves, its served name, or the public-source label;
 * "unknown" only for a platform row that names neither. */
export function endpointLabel(ep: LabelledEndpoint): string {
  if (ep.isPublic) return publicEndpointLabel(ep);
  return knownName(ep.model) ?? knownName(ep.servedModelName) ?? "unknown";
}

/** "configured" for a public-source row, the mapped status otherwise. */
export function endpointStateText(ep: Pick<UiEndpoint, "status" | "isPublic">): string {
  return ep.isPublic ? PUBLIC_ENDPOINT_STATE : ep.status;
}
