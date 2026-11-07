/**
 * Diagnostics for the R4 language-model streaming path.
 *
 * Two loggers share one "R4 LM Stream" output channel:
 *  - RequestLifecycle: always on, metadata only (no message content). Reveals
 *    whether the provider is invoked, first-byte latency, and whether a request
 *    finishes quickly while the Copilot UI keeps spinning at "Optimizing tool
 *    selection".
 *  - getStreamDebugLogger: opt-in (r4.debugStream), logs per-delta content.
 */

import * as vscode from "vscode";
import type { StreamDelta } from "./gateway-client";

let streamChannel: vscode.OutputChannel | undefined;

function lmStreamChannel(): vscode.OutputChannel | undefined {
  if (typeof vscode.window?.createOutputChannel !== "function") {
    return undefined;
  }
  if (!streamChannel) {
    streamChannel = vscode.window.createOutputChannel("R4 LM Stream");
  }
  return streamChannel;
}

/** Metadata-only lifecycle tracker for a single chat-completion request. */
export class RequestLifecycle {
  private readonly startedAt = Date.now();
  private firstByteMs = -1;
  private deltas = 0;
  private reasoningChars = 0;
  private contentChars = 0;
  private toolCallDeltas = 0;

  constructor(private readonly modelId: string) {}

  begin(meta: string): void {
    this.line(`[req] model=${this.modelId} ${meta}`);
  }

  onDelta(delta: StreamDelta): void {
    if (this.firstByteMs < 0) {
      this.firstByteMs = Date.now() - this.startedAt;
    }
    this.deltas++;
    if (delta.reasoning) {
      this.reasoningChars += delta.reasoning.length;
    }
    if (delta.content) {
      this.contentChars += delta.content.length;
    }
    if (delta.tool_calls) {
      this.toolCallDeltas++;
    }
  }

  end(cancelled: boolean, emittedToolCalls: number): void {
    this.line(
      `[done] model=${this.modelId} ${this.stats()} ` +
        `emittedToolCalls=${emittedToolCalls} cancelled=${cancelled}`,
    );
  }

  error(err: unknown): void {
    this.line(`[error] model=${this.modelId} ${this.stats()} err=${String(err)}`);
  }

  private stats(): string {
    return (
      `dur=${Date.now() - this.startedAt}ms firstByte=${this.firstByteMs}ms ` +
      `deltas=${this.deltas} reasoningChars=${this.reasoningChars} ` +
      `contentChars=${this.contentChars} toolCallDeltas=${this.toolCallDeltas}`
    );
  }

  private line(text: string): void {
    lmStreamChannel()?.appendLine(`${new Date().toISOString()} ${text}`);
  }
}

/** Opt-in per-delta content logger (gated by the r4.debugStream setting). */
export function getStreamDebugLogger(): ((delta: StreamDelta) => void) | undefined {
  const enabled = vscode.workspace
    .getConfiguration("r4")
    .get<boolean>("debugStream", false);
  if (!enabled) {
    return undefined;
  }
  const channel = lmStreamChannel();
  if (!channel) {
    return undefined;
  }
  return (delta: StreamDelta) => {
    const fields: string[] = [];
    if (delta.reasoning !== undefined) {
      fields.push(`reasoning=${JSON.stringify(delta.reasoning)}`);
    }
    if (delta.content !== undefined) {
      fields.push(`content=${JSON.stringify(delta.content)}`);
    }
    if (delta.tool_calls) {
      fields.push(`tool_calls=${JSON.stringify(delta.tool_calls)}`);
    }
    if (delta.usage) {
      fields.push(`usage=${JSON.stringify(delta.usage)}`);
    }
    if (fields.length > 0) {
      channel.appendLine(fields.join(" "));
    }
  };
}
