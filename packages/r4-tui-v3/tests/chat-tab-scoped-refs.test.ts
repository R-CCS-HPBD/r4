/**
 * H-7 stage 2 ratchet: `workspace.<chat-scoped-field>` reads outside the
 * swap's home may only SHRINK as readers migrate to `activeTab()`.
 *
 * Baseline 2026-10-19: 0 hits — every reader migrated in T1 (14 files),
 * T2 (8), T3a (13), T3b (5), T4a (5), T4b (2) and T5 (chat.tsx);
 * count measured by this
 * test's own scanner with the full 47-field list. The swap's own
 * plumbing (`stores/index.ts`, `tabs/registry.ts`) is exempt; the singleton
 * accessor `activeTab()` is the migration target, and `tabs/chat-tab-store.ts`
 * owns the field list (it never reads the singleton).
 *
 * Two pins:
 * 1. the same 14 migrated files stay at zero;
 * 2. the site-wide total never grows past the baseline.
 */

import { describe, expect, it } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { CHAT_SCOPED_FIELDS } from "../src/tabs/chat-tab-store";

const SRC = join(__dirname, "..", "src");
const FIELD_ALT = CHAT_SCOPED_FIELDS.join("|");
const REF = new RegExp(`(?<![A-Za-z])workspace\\.(${FIELD_ALT})\\b`, "g");

/** The swap's own code may read scoped fields from the singleton. */
const EXEMPT = new Set(["stores/index.ts", "tabs/registry.ts"]);

/** The migration tranches (2026-10-19): pinned at zero.
 * T1: single-read; T2: 2-read; T3a: 3-4-read; T3b: 5-7; T4a: 8-10;
 * T4b: header/status-bar; T5: chat.tsx. Stage 2 reader migration COMPLETE
 * for readers outside the swap plumbing; stage 3 routes writes. */
const MIGRATED = [
  "adapters/r4-cli.ts",
  "commands/index.ts",
  "lib/resolve-verification.ts",
  "lib/interaction-mode.ts",
  "lib/clear-conversation.ts",
  "lib/research-keyboard.ts",
  "stores/science.ts",
  "app.tsx",
  "components/ask-user-overlay.tsx",
  "components/greeting.tsx",
  "components/approval-gates.tsx",
  "components/mission-control.tsx",
  "components/rewind-overlay.tsx",
  "routes/settings.tsx",
  "bridge/ask-user-bridge.ts",
  "bridge/plan-bridge.ts",
  "components/tool-approval-overlay.tsx",
  "hooks/useBackend.ts",
  "lib/chat-input-focus.ts",
  "lib/loop-run-result.ts",
  "lib/space-shortcuts.ts",
  "terminal/interceptor.ts",
  "commands/slash-pickers.ts",
  "commands/wave-intercepts.ts",
  "components/mascot.tsx",
  "components/plan-card.tsx",
  "hooks/project-refresh.ts",
  "hooks/useKnowledge.ts",
  "keymap/store-claims.ts",
  "stores/umrs-workflow.ts",
  "bridge/companion-runtime-bridge.ts",
  "bridge/session-bridge.ts",
  "commands/project-job-events.ts",
  "components/list-overlay.tsx",
  "routes/knowledge.tsx",
  "adapters/companion-chat.ts",
  "layout/context-panel.tsx",
  "lib/start-research.ts",
  "routes/work.tsx",
  "layout/activity-console.tsx",
  "keymap/index.ts",
  "commands/slash.ts",
  "bridge/approval-bridge.ts",
  "bridge/venue-bridge.ts",
  "components/agent-activity.tsx",
  "layout/header.tsx",
  "layout/status-bar.tsx",
  "components/chat.tsx",
];

const BASELINE_TOTAL = 0;

function* sources(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) {
      yield* sources(full);
    } else if (full.endsWith(".ts") || full.endsWith(".tsx")) {
      yield full;
    }
  }
}

function scopedRefs(path: string): number {
  return (readFileSync(path, "utf8").match(REF) ?? []).length;
}

describe("chat-scoped workspace refs ratchet (H-7 stage 2)", () => {
  it("migrated files keep zero scoped refs", () => {
    const dirty = MIGRATED.filter((rel) => scopedRefs(join(SRC, rel)) > 0);
    expect(dirty).toEqual([]);
  });

  it("the site-wide total never grows past the baseline", () => {
    let total = 0;
    for (const file of sources(SRC)) {
      if (EXEMPT.has(relative(SRC, file))) continue;
      total += scopedRefs(file);
    }
    expect(total).toBeLessThanOrEqual(BASELINE_TOTAL);
  });
});
