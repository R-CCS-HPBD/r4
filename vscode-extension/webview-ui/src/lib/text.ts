/**
 * Text helpers for transcript rendering.
 *
 * Markdown itself is rendered by react-markdown inside MessageBubble; this
 * module only normalises raw model output before it reaches the transcript.
 */

/**
 * Remove reasoning/thinking markers that leaked into the visible answer.
 *
 * The ACP server already splits reasoning into `session/thinking`
 * notifications, so anything left here is a stray marker from a model that
 * emitted the tags inline.
 */
export function stripThinking(text: string): string {
  return text
    .replace(/`+?think`+?(.*?)`+?endthink`+?/gs, '')
    .replace(/<think>.*?<\/think>/gs, '')
    .trim();
}
