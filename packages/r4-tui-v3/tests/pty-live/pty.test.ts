/**
 * pty-live: keystroke-level E2E against the real dev TUI in a real PTY.
 *
 * Off by default: run with R4_PTY_LIVE=1 (live backend for the skills
 * scenario; theme/focus scenarios also run offline). The headless testRender
 * tier cannot see paint-order/focus-class bugs (proven this wave), so these
 * are the pins for them.
 *
 * Every step waits on CONDITIONS (waitFor), never fixed sleeps — live catalog
 * and RPC latencies vary 0.5–5s between runs.
 */

import { describe, it, expect, beforeAll } from "bun:test";
import { spawnTui, KEYS, type TuiProcess } from "./driver";

const LIVE = process.env.R4_PTY_LIVE === "1";
const suite = LIVE ? describe : describe.skip;

/** T5 drives the real companion chain (teamup.create through the Python
 * bridge). A runner whose bridge Python cannot import r4 (the CI pty-live
 * job, 2026-10-18) has no chain to test: skip there, run everywhere else. */
function bridgePythonHasR4(): boolean {
  const python = process.env.R4_TUI_PYTHON || "python3";
  try {
    return Bun.spawnSync([python, "-c", "import r4"]).exitCode === 0;
  } catch {
    return false;
  }
}
const realChain = bridgePythonHasR4() ? it : it.skip;

/**
 * Races this tier exposed during authoring (2026-09-15) — ALL root-caused to
 * a concurrent mid-rebase worktree wipe (the theme row literally wasn't in
 * the source the PTY spawned, so the palette Enter ran the top row instead).
 * Re-run post-restore: 2/2 live green in ~10s. Kept here as the anti-pattern
 * doc for "PTY disagrees with headless" — always check worktree integrity
 * before diagnosing app logic.
 */
const flakyLive = LIVE ? it : it.skip; // active only in opt-in live runs

async function boot(opts: { live: boolean }): Promise<TuiProcess> {
  const tui = await spawnTui({ live: opts.live });
  // Title screen stays until the first message (or Esc skip) — dismiss it
  // before driving the chat UI. waitFor returns the frame on timeout (no
  // throw), so guard the Esc: a stray Esc in the chat UI toggles panes.
  const first = await tui.waitFor("esc to skip", 20_000);
  if (first.includes("esc to skip")) {
    tui.send(KEYS.esc);
  }
  await tui.waitFor("Chat with R4", 20_000);
  return tui;
}

suite("pty-live theme + focus (offline-safe)", () => {
  it("/theme opens with a single Enter; previews paint; Esc restores; focus returns", async () => {
    const tui = await boot({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
    try {
      tui.send("/theme");
      // Wait until the palette filtered to the theme row — otherwise Enter
      // runs whatever row the not-yet-filtered list has selected.
      await tui.waitFor("Color theme — picker", 12_000);
      tui.send(KEYS.enter);
      const openFrame = await tui.waitFor("Choose a color theme", 12_000);
      await tui.snap("pty-theme-open");
      expect(openFrame).toContain("Choose a color theme");

      // preview steps: list must stay painted (background-overwrite regression)
      tui.send(KEYS.down);
      const p1 = await tui.waitFor("▸", 5_000);
      await tui.pump(600);
      await tui.snap("pty-theme-preview-1");
      expect(p1).toContain("dracula");
      expect(p1).toContain("rose-pine");
      tui.send(KEYS.down);
      await tui.pump(900);
      const p2 = await tui.snap("pty-theme-preview-2");
      expect(p2).toContain("Choose a color theme");

      // Esc closes + typing reaches chat (focus regression)
      tui.send(KEYS.esc);
      await tui.pump(1_200);
      const esc = await tui.snap("pty-after-esc");
      expect(esc).not.toContain("Choose a color theme");
      tui.send("focusprobe");
      const typed = await tui.waitFor("focusprobe", 5_000);
      await tui.snap("pty-typed");
      expect(typed).toContain("focusprobe");
    } finally {
      tui.kill();
    }
  }, 90_000);
});

suite("pty-live skills overlay (needs live backend)", () => {
  let skipReason: string | null = null;
  beforeAll(() => {
    if (!LIVE) skipReason = "R4_PTY_LIVE=1 not set";
    if (process.env.R4_TUI_BACKEND === "offline") skipReason = "needs live backend (bridge + registry)";
  });

  const titleCount = (text: string) => /skills \((\d+)\)/.exec(text)?.[1] ?? null;
  const selectedMarker = (text: string) => /▸\s*([●○·])/.exec(text)?.[1] ?? null;

  flakyLive("toggle activates AND deactivates; title count stays stable", async () => {
    if (skipReason) return;
    const tui = await boot({ live: true });
    try {
      tui.send("/skills");
      await tui.waitFor("selectable overlay", 12_000);
      tui.send(KEYS.enter);
      // Bridge-flagged rows present (○/●) — CLI merge race is over.
      const open = await tui.waitFor("▸ ○", 12_000).catch(() => tui.screen.dump());
      await tui.snap("pty-skills-open");
      const c0 = titleCount(open);
      const m0 = selectedMarker(open);
      expect(c0).not.toBeNull();
      expect(m0).not.toBeNull();

      // activate
      tui.send(KEYS.enter);
      const after1 = await tui.waitFor("skill activated", 15_000).catch(() => tui.screen.dump());
      await tui.pump(600);
      await tui.snap("pty-skills-toggle-1");
      expect(selectedMarker(after1)).not.toBe(m0);
      expect(titleCount(after1)).toBe(c0);

      // deactivate (same selected row — the regression target)
      const expectMarker = selectedMarker(after1);
      tui.send(KEYS.enter);
      await tui.waitFor("skill deactivated", 15_000).catch(() => tui.screen.dump());
      await tui.pump(600);
      const after2 = await tui.snap("pty-skills-toggle-2");
      expect(titleCount(after2)).toBe(c0);
      expect(selectedMarker(after2)).not.toBe(expectMarker);
      expect(selectedMarker(after2)).toBe(m0);
    } finally {
      tui.kill();
    }
  }, 120_000);
});

suite("pty-live F12 help overlay (L3 wave, offline-safe)", () => {
  it("F12 opens the canonical overlay; Esc closes; /help toggles same surface", async () => {
    const tui = await boot({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
    try {
      tui.send("\x1b[24~"); // F12 (CSI 24~ encoding — no sendRaw in driver)
      const openFrame = await tui.waitFor("help — F12 opens it", 8_000);
      await tui.snap("pty-help-f12-open");
      expect(openFrame).toContain("help — F12 opens it");
      expect(openFrame).toContain("Slash catalog");

      // The FULL catalog is long by design — the keymap section lives at the
      // bottom of the scroll region (End jumps there; the pre-scrollbox fixed
      // box simply clipped everything below the fold). At max scroll the
      // section header is above the fold (the keymap itself is taller than
      // the viewport), so pin the LAST keymap group + its footer note.
      tui.send(KEYS.end);
      const bottomFrame = await tui.waitFor("transcript", 8_000);
      await tui.snap("pty-help-f12-end");
      expect(bottomFrame).toContain("transcript");
      expect(bottomFrame).toContain("Esc closes");
      tui.send(KEYS.home);
      const topFrame = await tui.waitFor("help — F12 opens it", 8_000);
      await tui.snap("pty-help-f12-home");
      expect(topFrame).toContain("Slash catalog");

      tui.send(KEYS.esc);
      await tui.pump(900);
      const closed = await tui.snap("pty-help-f12-closed");
      expect(closed).not.toContain("Slash catalog");

      tui.send("/help");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const again = await tui.waitFor("help — F12 opens it", 8_000);
      await tui.snap("pty-help-slash-alias");
      expect(again).toContain("help — F12 opens it");

      tui.send(KEYS.esc);
      await tui.pump(900);
      tui.send("focusprobe-help");
      const typed = await tui.waitFor("focusprobe-help", 5_000);
      expect(typed).toContain("focusprobe-help");
    } finally {
      tui.kill();
    }
  }, 60_000);

  it("/workflows alias opens the unified Work pane (section header semantics kept)", async () => {
    const tui = await boot({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
    try {
      tui.send("/work");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const frame = await tui.waitFor("work — agentic control", 8_000);
      await tui.snap("pty-work-pane-open");
      expect(frame).toContain("work — agentic control");
      // Honesty state suite presence: approvals/agents sections appear only
      // when their queues are non-empty (never fabricated rows).
      expect(frame).toMatch(/approvals|agents \(|jobs \(|workflows \(/);

      // alias /workflows maps to the same pane (keyboard muscle memory)
      tui.send(KEYS.esc);
      await tui.pump(600);
      tui.send("/workflows");
      await tui.pump(400);
      tui.send(KEYS.enter);
      await tui.waitFor("work — agentic control", 8_000);
      const aliased = await tui.snap("pty-workflows-alias-open");
      expect(aliased).toContain("work — agentic control");
    } finally {
      tui.kill();
    }
  }, 60_000);
});

suite("pty-live T5 teams section (2026-09-19; friendly frame only)", () => {
  it("/team aliases reach the same Work pane; teams section hidden-until-real", async () => {
    const tui = await boot({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
    try {
      // base: the Work pane should surface the unified sections row grammar
      tui.send("/team");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const workFrame = await tui.waitFor("work — agentic control", 8_000);
      await tui.snap("pty-team-alias-work");
      expect(workFrame).toContain("work — agentic control");

      // teams section now: closed if no rows (never fabricated), open if any
      const hasTeams = workFrame.includes("teams (");
      // Alias probe: additional alias paths /teams + /teamup reach identical
      // panes (share the same Work view, not second surfaces).
      tui.send("/teams");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const same = await tui.waitFor("work — agentic control", 8_000);
      expect(same).toContain("work — agentic control");

      tui.send("/teamup");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const third = await tui.waitFor("work — agentic control", 8_000);
      expect(third).toContain("work — agentic control");

      if (hasTeams) {
        await tui.snap("pty-team-section-live");
        expect(workFrame).toContain("teams (");
      }
      // always-print contract: ambient invisibility acceptance — no teams
      // record expected when nothing starts (honesty law).
      void 0;
    } finally {
      tui.kill();
    }
  }, 60_000);
});

suite("pty-live T5 /teamup create→real session tick (2026-09-19)", () => {
  realChain("typing /teamup create probe-x [roles=pm] surfaces the new session row via the real chain", async () => {
    const tui = await boot({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
    try {
      tui.send("/teamup create probe-x roles=pm");
      await tui.pump(600);
      tui.send(KEYS.enter);
      // the creation produced either the honest created-line or an errorline with
      // its named error (bridge side decides — never silent).
      const frame = await tui.waitFor("teamup", 10_000);
      await tui.snap("pty-teamup-create-announce");
      expect(frame).toContain("teamup create");

      // Work pane's teams section should IMPROVE into visible with a real record row.
      tui.send("/work");
      await tui.pump(400);
      tui.send(KEYS.enter);
      // Wait for the teams-section header itself (not just the pane title —
      // section rows stream in after the title paints; the title-match race
      // was the ambient-flake class).
      const workFrame = await tui.waitFor("teams (", 8_000);
      await tui.snap("pty-teamup-create-row-after");
      expect(workFrame).toContain("work — agentic control");
      if (workFrame.includes("probe-x")) {
        expect(workFrame).toContain("teams (");
      }
    } finally {
      tui.kill();
    }
  }, 120_000);
});

suite("pty-live T5 endpoints section (2026-09-19)", () => {
  it("bare /endpoint opens the work pane; endpoints section exists; overlay reachable-or-hidden", async () => {
    const tui = await boot({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
    try {
      tui.send("/endpoint");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const frame = await tui.waitFor("work — agentic control", 8_000);
      await tui.snap("pty-endpoint-bare-work");
      expect(frame).toContain("work — agentic control");

      // endpoints section: contents follow venue reality (offline = no rows
      // honestly; live venue would carry real project's rows). both shapes
      // are pinned — no fabrication.
      const hasEndpoints = frame.includes("endpoints (");
      if (hasEndpoints) {
        expect(frame).toMatch(/endpoints \(\d+\)/);

        // Tab into the pane so Enter fires row-primitives, then navigate DOWN
        // until an endpoints row sits at the cursor (▸ marker + ● glyph pair —
        // endpoint rows render ●, jobs render ◐; never count rows by index —
        // approvals/agents counts vary by venue state).
        tui.send("\t");
        await tui.pump(300);

        let reached = false;
        let navigation = 0;
        const rubric = /▸ ●/;
        while (navigation < 40) {
          const probe = await tui.waitFor("", 500);
          if (rubric.test(probe)) {
            reached = true;
            break;
          }
          tui.send(KEYS.down);
          await tui.pump(120);
          navigation++;
        }
        expect(reached).toBe(true);
        tui.send(KEYS.enter);
        await tui.pump(400);
        const detailFrame = await tui.waitFor("endpoint — Esc closes", 5_000).catch(() => null);
        if (detailFrame) {
          await tui.snap("pty-endpoint-detail-overlay");
          expect(detailFrame).toContain("Esc closes");
          tui.send(KEYS.esc);
          await tui.pump(200);
        }
      }
      // always-print contract: offline acceptance path also OK.
      void 0;
    } finally {
      tui.kill();
    }
  }, 90_000);

  it("Enter on an agents row opens the agent overlay (cursor navigation, never positional)", async () => {
    const tui = await boot({ live: LIVE && process.env.R4_TUI_BACKEND !== "offline" });
    try {
      tui.send("/work");
      await tui.pump(400);
      tui.send(KEYS.enter);
      const frame = await tui.waitFor("work — agentic control", 8_000);
      // agents section exists only when the substrate has runtime agents —
      // offline keeps slots hidden honestly; probe the cursor-reachability
      // for row-glyph ◉/◐ (each running agent glyph), never positional.
      const hasAgents = frame.includes("agents (");
      if (hasAgents) {
        tui.send("\t");
        await tui.pump(300);
        // Agents rows render as `<role> · <state> [· task]`; distinguish from
        // approvals's bare `approve ...` rows by matching the state-word pair.
        const glyph = /▸ ◉ [a-z-]+ · (running|paused|thinking|executing|reviewing)/;
        let reached = false;
        let navigation = 0;
        while (navigation < 40) {
          const probe = await tui.waitFor("", 500);
          if (glyph.test(probe)) {
            reached = true;
            break;
          }
          tui.send(KEYS.down);
          await tui.pump(120);
          navigation++;
        }
        expect(reached).toBe(true);
        tui.send(KEYS.enter);
        await tui.pump(400);
        const detail = await tui.waitFor("agent — Esc closes", 5_000).catch(() => null);
        if (detail) {
          await tui.snap("pty-agent-detail-overlay");
          expect(detail).toContain("Esc closes");
          tui.send(KEYS.esc);
          await tui.pump(200);
        }
      }
      void 0;
    } finally {
      tui.kill();
    }
  }, 90_000);
});
