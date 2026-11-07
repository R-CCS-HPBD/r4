import * as vscode from "vscode";

export const R4_REPLAY_MARKER_MIME = "application/vnd.r4.replay-marker";
export const R4_REPLAY_MARKER_WRITER_ID = "r4-replay-marker";
const ENCODED_JSON_MARKER_PREFIX = "j:";

export interface ReplayMarkerMetadata {
  reasoningText?: string;
}

export interface ReplayMarkerParseResult {
  valid: boolean;
  reasoningText?: string;
  error?: string;
}

/** Encode reasoning text into a replay marker payload (no VS Code dependency). */
export function encodeReplayMarkerBytes(metadata: ReplayMarkerMetadata): Uint8Array | undefined {
  if (!metadata.reasoningText) {
    return undefined;
  }
  const payload = JSON.stringify({ reasoning: { text: metadata.reasoningText } });
  const encoded = Buffer.from(payload, "utf8").toString("base64url");
  return new TextEncoder().encode(`${R4_REPLAY_MARKER_WRITER_ID}\\${ENCODED_JSON_MARKER_PREFIX}${encoded}`);
}

export function parseReplayMarkerData(data: Uint8Array): ReplayMarkerParseResult {
  const decoded = new TextDecoder().decode(data);
  const separatorIndex = decoded.indexOf("\\");
  if (separatorIndex < 0) {
    return { valid: false, error: "marker-prefix-missing" };
  }
  const markerPrefix = decoded.slice(0, separatorIndex);
  if (markerPrefix !== R4_REPLAY_MARKER_WRITER_ID) {
    return { valid: false, error: "marker-prefix-mismatch" };
  }
  const markerPayload = decoded.slice(separatorIndex + 1);
  if (!markerPayload.startsWith(ENCODED_JSON_MARKER_PREFIX)) {
    return { valid: false, error: "marker-payload-format" };
  }
  const encodedPayload = markerPayload.slice(ENCODED_JSON_MARKER_PREFIX.length);
  if (!encodedPayload) {
    return { valid: false, error: "marker-payload-empty" };
  }
  try {
    const json = Buffer.from(encodedPayload, "base64url").toString("utf8");
    const value = JSON.parse(json) as unknown;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { valid: false, error: "marker-payload-not-object" };
    }
    const reasoning = (value as { reasoning?: unknown }).reasoning;
    if (!reasoning || typeof reasoning !== "object" || Array.isArray(reasoning)) {
      return { valid: true };
    }
    const text = (reasoning as { text?: unknown }).text;
    if (typeof text !== "string" || text.length === 0) {
      return { valid: true };
    }
    return { valid: true, reasoningText: text };
  } catch {
    return { valid: false, error: "marker-json-invalid" };
  }
}

export function parseReplayMarkerPart(part: unknown): ReplayMarkerParseResult | undefined {
  if (!(part instanceof vscode.LanguageModelDataPart)) {
    return undefined;
  }
  if (part.mimeType !== R4_REPLAY_MARKER_MIME) {
    return undefined;
  }
  return parseReplayMarkerData(part.data);
}

export function createReplayMarkerPart(
  metadata: ReplayMarkerMetadata,
): vscode.LanguageModelDataPart | undefined {
  const bytes = encodeReplayMarkerBytes(metadata);
  if (!bytes) {
    return undefined;
  }
  return new vscode.LanguageModelDataPart(bytes, R4_REPLAY_MARKER_MIME);
}

export function parseFirstReplayMarkerReasoning(
  message: vscode.LanguageModelChatRequestMessage,
): string | undefined {
  for (const part of message.content) {
    const parsed = parseReplayMarkerPart(part);
    if (parsed?.valid && parsed.reasoningText) {
      return parsed.reasoningText;
    }
  }
  return undefined;
}
