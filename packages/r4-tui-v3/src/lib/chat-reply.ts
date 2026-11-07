/**
 * Local chat reply generator used when no live chat backend is reachable.
 * Must never claim side effects (indexing, plans, tasks) that did not run.
 */

export type LocalReplyReason = "offline" | "companion-unavailable";

const offlineReplies = [
  "[Offline] Noted. No backend action ran — this is fixture/demo chat only.",
  "[Offline] Local reply only. Tasks and Knowledge will not update from this message.",
  "[Offline] Demo chat: I am not indexing files or creating plans.",
  "[Offline] Got it as conversation context only. No project pipeline steps were started.",
];

/** True when the failure is an HTTP 5xx from the provider/gateway API —
 * the bridge and Companion are fine; the upstream model backend failed.
 * The r4 client renders these as ``API error: 5xx[: server detail]``
 * (_http._api_error_message); also match bare ``HTTP 5xx`` / ``bad gateway``
 * spellings other adapters may carry. */
export function isProviderServerFailure(detail?: string): boolean {
  const d = (detail ?? "").toLowerCase();
  if (!d) return false;
  return /\bapi error:\s*5\d\d\b/.test(d) || /\bhttp 5\d\d\b/.test(d) || d.includes("bad gateway");
}

/** True when the provider/gateway dropped the HTTP stream mid-reply (httpx
 * RemoteProtocolError / reset): the bridge is fine, the reply is cut
 * (stabilization sweep 2026-10-21, chat#7 — it read "bridge failed"). */
export function isProviderStreamDropped(detail?: string): boolean {
  return /peer closed connection|incomplete chunked read|incomplete message body|server disconnected|remoteprotocolerror|connection reset/i.test(
    detail ?? "",
  );
}

/** True when the r4 client's own read timeout fired while it waited on the
 * model endpoint (R4TimeoutError "Request timed out: …"). */
export function isClientReadTimeout(detail?: string): boolean {
  return /timed out/i.test(detail ?? "");
}

/** True when the failure is gateway/LLM network (Companion itself is up). */
export function isGatewayNetworkFailure(detail?: string): boolean {
  const d = (detail ?? "").toLowerCase();
  if (!d) return false;
  return (
    d.includes("handshake") ||
    d.includes("_ssl") ||
    d.includes("ssl") ||
    d.includes("gateway unreachable") ||
    d.includes("failed to reach gateway") ||
    d.includes("connect timeout") ||
    d.includes("connection timed out") ||
    d.includes("name or service not known") ||
    d.includes("nodename nor servname") ||
    d.includes("getaddrinfo") ||
    d.includes("network is unreachable") ||
    d.includes("temporary failure in name resolution")
  );
}

/**
 * Honest copy when chat cannot complete.
 * Distinguishes Companion process failure vs gateway/LLM SSL/network timeout.
 */
export function companionUnavailableReply(detail?: string): string {
  const tip = detail?.trim() ? ` (${truncateDetail(detail)})` : "";
  if (isGatewayNetworkFailure(detail)) {
    return (
      `[Chat unavailable] LLM/gateway network failed${tip}. ` +
      `Companion is running; the active gateway or model endpoint did not complete TLS. ` +
      `Run \`r4 doctor\` / \`r4 health\`, check reachability of your gateway and model endpoint (\`r4 source list\`), ` +
      `or use \`r4 console --offline\` for demo chat.`
    );
  }
  if (isProviderServerFailure(detail)) {
    // No retry claim: whether one ran is not known here (chat#10 — the copy
    // said "already retried" after a single streamed request).
    return (
      `[Chat unavailable] LLM provider returned a server error${tip}. ` +
      `The bridge and Companion are fine — the upstream model backend failed. ` +
      `Retry the turn, switch model with /model, ` +
      `or check \`r4 health\` / \`r4 endpoint list\`.`
    );
  }
  const upstream = upstreamFailureReply(detail, tip) ?? bridgeExitReply(detail, tip);
  if (upstream) return upstream;
  return (
    `[Chat unavailable] Research Companion bridge failed${tip}. ` +
    `Inventory can still be live via r4-cli; chat needs Companion. ` +
    `Check companion is installed/running, or use \`r4 console --offline\` for explicit demo chat.`
  );
}

/** The model endpoint broke off or went silent mid-turn: the bridge and
 * Companion are fine and any text already shown is incomplete (chat#7/#12). */
function upstreamFailureReply(detail: string | undefined, tip: string): string | null {
  if (isProviderStreamDropped(detail)) {
    return (
      `[Chat unavailable] The model provider closed the stream mid-reply${tip}. ` +
      `The bridge and Companion are fine; any reply text above is incomplete. ` +
      `Retry the turn, or switch model with /model.`
    );
  }
  if (isClientReadTimeout(detail)) {
    return (
      `[Chat unavailable] The model endpoint sent no data within the r4 client's read timeout${tip}. ` +
      `The bridge and Companion are fine; any reply text above is incomplete. ` +
      `Retry the turn, switch model with /model, or raise R4_TIMEOUT (seconds) for a slow provider.`
    );
  }
  return null;
}

/** The bridge process ended during the turn (killed, crashed): the next
 * message spawns a new one, so "is companion installed? use --offline" was
 * wrong advice (c2-tui 2026-10-21, PTY journey j11). */
function bridgeExitReply(detail: string | undefined, tip: string): string | null {
  if (!/^bridge exited\b/i.test(detail?.trim() ?? "")) return null;
  return (
    `[Chat unavailable] The Research Companion bridge exited during this turn${tip}. ` +
    `Any reply text above is incomplete. Your next message starts a new bridge; ` +
    `if it exits again, run \`r4 doctor\` (is Companion installed, is R4_TUI_PYTHON right?).`
  );
}

function truncateDetail(detail: string, max = 120): string {
  const oneLine = detail.replace(/\s+/g, " ").trim();
  return oneLine.length <= max ? oneLine : `${oneLine.slice(0, max - 1)}…`;
}

export function generateLocalReply(
  userText: string,
  turn: number,
  reason: LocalReplyReason = "offline",
): string {
  if (reason === "companion-unavailable") {
    return companionUnavailableReply();
  }

  const text = userText.toLowerCase();

  if (text.includes("file") || text.includes("material") || text.includes("paper") || text.includes("upload")) {
    return `[Offline] I cannot upload or index “${userText.slice(0, 30)}…”. Use \`r4 project ingest\` or live Companion chat — no indexing started.`;
  }

  if (text.includes("plan") || text.includes("goal") || text.includes("task")) {
    return `[Offline] I cannot create a research plan from “${userText.slice(0, 30)}…”. Use \`r4 project plan\` / Companion chat — Tasks were not updated.`;
  }

  if (text.includes("hi") || text.includes("hello") || text.includes("hey")) {
    return "[Offline] Hello — this is demo/fixture chat, not Companion. Real replies need the Research Companion bridge.";
  }

  return offlineReplies[turn % offlineReplies.length]!;
}
