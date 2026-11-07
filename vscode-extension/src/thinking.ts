import * as vscode from "vscode";

export { ThinkingStreamGate } from "./think-gate";

type ThinkingPartCtor = new (
  value: string | string[],
  id?: string,
  metadata?: { readonly [key: string]: unknown },
) => vscode.LanguageModelResponsePart;

type ThinkingDelta = {
  text?: string | string[];
  id?: string;
  metadata?: { readonly [key: string]: unknown };
};

type ThinkingProgressStream = vscode.ChatResponseStream & {
  thinkingProgress(delta: ThinkingDelta): void;
};

const THINKING_METADATA = { title: "Thinking" } as const;
let nextThinkingSequence = 1;

export function createThinkingSequenceId(): string {
  return `r4-thinking-${nextThinkingSequence++}`;
}

/** Whether this VS Code build exposes LanguageModelThinkingPart. */
export function supportsLanguageModelThinkingPart(): boolean {
  return typeof (vscode as { LanguageModelThinkingPart?: ThinkingPartCtor })
    .LanguageModelThinkingPart === "function";
}

/** Stream reasoning into VS Code's collapsible thinking UI (LM provider path). */
export function reportLanguageModelThinking(
  progress: vscode.Progress<vscode.LanguageModelResponsePart>,
  text: string,
  id: string,
): void {
  const Ctor = (vscode as { LanguageModelThinkingPart?: ThinkingPartCtor })
    .LanguageModelThinkingPart;
  if (Ctor) {
    progress.report(new Ctor(text, id, THINKING_METADATA));
  }
}

/**
 * Finalize the active thinking block so VS Code closes it before answer text or
 * tool calls render. Mirrors vscode-copilot-chat languageModelAccess.ts.
 */
export function reportLanguageModelThinkingDone(
  progress: vscode.Progress<vscode.LanguageModelResponsePart>,
): void {
  const Ctor = (vscode as { LanguageModelThinkingPart?: ThinkingPartCtor })
    .LanguageModelThinkingPart;
  if (Ctor) {
    progress.report(new Ctor("", "", { vscode_reasoning_done: true }));
  }
}

/** Whether the chat participant stream supports thinkingProgress. */
export function supportsThinkingProgress(
  stream: vscode.ChatResponseStream,
): stream is ThinkingProgressStream {
  return typeof (stream as ThinkingProgressStream).thinkingProgress === "function";
}

/** Stream reasoning into VS Code's collapsible thinking UI (@r4 participant path). */
export function reportChatThinkingProgress(
  stream: vscode.ChatResponseStream,
  text: string,
  id: string,
): void {
  if (supportsThinkingProgress(stream)) {
    stream.thinkingProgress({ text, id, metadata: THINKING_METADATA });
  }
}
