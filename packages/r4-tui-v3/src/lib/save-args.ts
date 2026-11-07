/**
 * `/save` arguments and the publish confirmation's wording (K1.6, L3).
 * Pure (ADR-103 D-3, 2026-11-01): the console's `commands/save-command.ts`
 * re-exports it and the VS Code extension's bridge host uses it.
 */

const TURN_TOKEN = /^(t?\d+|last)$/i;
const PROMPT_WORDS = new Set(["mine", "--mine", "prompt", "--prompt"]);

/** True when the line asks for a turn save; false for a transcript path. */
export function isTurnSave(args: string): boolean {
  const first = args.trim().split(/\s+/)[0] ?? "";
  if (!first) return true;
  return TURN_TOKEN.test(first) || first.startsWith("--") || PROMPT_WORDS.has(first.toLowerCase());
}

export const PUBLISH_CONFIRM_WINDOW_MS = 8_000;

/** What a save does, as both surfaces say it before the first one. */
export const PUBLISH_EXPLANATION =
  "save: this publishes the text to the project's knowledge graph, where project members can read it.";

export function publishConfirmNotice(again: string): string {
  return (
    `${PUBLISH_EXPLANATION} ` +
    `${again} within ${PUBLISH_CONFIRM_WINDOW_MS / 1000} s to save; later saves in this console run will not ask.`
  );
}
