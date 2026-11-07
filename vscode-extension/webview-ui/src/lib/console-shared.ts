/**
 * Console modules the webview runs as is (ADR-103 D-3): the `@` token being
 * typed, atomic chip deletion, and large-paste chips. All pure; the
 * extension's `test/shared-modules.test.mjs` keeps them free of Solid,
 * OpenTUI and Bun.
 */

export { extractMentionQuery, type MentionQuery } from '../../../../packages/r4-tui-v3/src/lib/mention-query';
export { collapseChipsOnBackspace } from '../../../../packages/r4-tui-v3/src/lib/mention-chips';
export { createPasteTracker, type PasteTracker } from '../../../../packages/r4-tui-v3/src/lib/paste-tracker';
