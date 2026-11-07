import * as vscode from "vscode";
import { R4_REPLAY_MARKER_MIME } from "./reasoning-replay";

const IMAGE_PART_ESTIMATED_CHARS = 1020;

type ThinkingPartLike = { value: string | string[] };

function isLanguageModelThinkingPart(part: unknown): part is ThinkingPartLike {
  const Ctor = (vscode as { LanguageModelThinkingPart?: new (...args: unknown[]) => unknown })
    .LanguageModelThinkingPart;
  return typeof Ctor === "function" && part instanceof Ctor;
}

function normalizeThinkingText(value: string | string[]): string {
  return Array.isArray(value) ? value.join("") : value;
}

function estimatePartChars(part: unknown): number {
  if (part instanceof vscode.LanguageModelTextPart) {
    return part.value.length;
  }
  if (part instanceof vscode.LanguageModelToolCallPart) {
    let chars = part.callId.length + part.name.length;
    try {
      chars += JSON.stringify(part.input).length;
    } catch {
      chars += 2;
    }
    return chars;
  }
  if (part instanceof vscode.LanguageModelToolResultPart) {
    let chars = part.callId.length;
    if (Array.isArray(part.content)) {
      for (const item of part.content) {
        chars += estimatePartChars(item);
      }
    }
    return chars;
  }
  if (part instanceof vscode.LanguageModelDataPart) {
    if (part.mimeType === R4_REPLAY_MARKER_MIME) {
      return 0;
    }
    // Internal cache-control metadata injected by VS Code or other
    // extensions. Must not be counted toward the token estimate.
    if (part.mimeType === "cache_control") {
      return 0;
    }
    if (part.mimeType.startsWith("image/")) {
      return IMAGE_PART_ESTIMATED_CHARS;
    }
    return Math.min(part.data?.byteLength ?? 0, 10_000);
  }
  if (isLanguageModelThinkingPart(part)) {
    return normalizeThinkingText(part.value).length;
  }
  if (part && typeof part === "object") {
    try {
      return JSON.stringify(part).length;
    } catch {
      return 0;
    }
  }
  return 0;
}

export function estimateTokenCount(
  text: string | vscode.LanguageModelChatRequestMessage,
  charsPerToken: number,
): number {
  const ratio = charsPerToken > 0 ? charsPerToken : 4;
  if (typeof text === "string") {
    return Math.max(1, Math.ceil(text.length / ratio));
  }
  if (!text?.content || !Array.isArray(text.content)) {
    return 1;
  }
  let totalChars = 0;
  for (const part of text.content) {
    totalChars += estimatePartChars(part);
  }
  return Math.max(1, Math.ceil(totalChars / ratio));
}

export function updateCharsPerToken(
  totalRequestChars: number,
  promptTokens: number,
  charsPerToken: number,
): number {
  if (totalRequestChars > 0 && promptTokens > 0) {
    const observedRatio = totalRequestChars / promptTokens;
    return charsPerToken * 0.7 + observedRatio * 0.3;
  }
  return charsPerToken;
}
