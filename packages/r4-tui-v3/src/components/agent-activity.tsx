/**
 * Agent work-state animation — the SINGLE live indicator, rendered in the
 * bottom status bar (the in-transcript row was removed: it duplicated the
 * status bar and split the user's eyes during a turn):
 *
 *   ⠋ Running shell_exec … ⏱ 12s · tools 3 · agents 1/2
 *   ⠋ thinking… ⏱ 3s
 *   ◉ awaiting approval: shell_exec
 *
 * Phase mapping (priority order):
 *   awaiting  — tool approval open (blinks)
 *   running   — a tool/command is executing (fast spin, green)
 *   thinking  — model streaming / reasoning (slow spin, cyan)
 *   idle      — nothing active (caller shows the idle left segment)
 *
 * Frames from cli-spinners (already a dependency); each state gets its own
 * cadence + color so state is readable at a glance without leaning on text.
 */

import { createSignal, onCleanup } from "solid-js";
import spinners from "cli-spinners";
import { workspace, activeTab } from "../stores";
import { decorativeMotion } from "../lib/motion";
import { palette } from "../themes";

export type AgentPhase = "awaiting" | "running" | "thinking" | "idle";

/** Pure phase derivation — unit-tested against store fixtures. */
export function agentPhase(): AgentPhase {
  if (activeTab().toolApproval) return "awaiting";
  if (activeTab().chatTurnStatus) return "running";
  if (activeTab().thinking.open || activeTab().chatStreaming || activeTab().isTyping) return "thinking";
  return "idle";
}

interface PhaseStyle {
  frames: string[];
  intervalMs: number;
  fg: string;
}

const PHASE_STYLE: Record<Exclude<AgentPhase, "idle">, PhaseStyle> = {
  running: { frames: spinners.dots10.frames, intervalMs: 80, fg: palette.statusOk },
  thinking: { frames: spinners.dots.frames, intervalMs: 120, fg: palette.knowledge },
  awaiting: { frames: ["◉", "◎"], intervalMs: 500, fg: palette.statusWarn },
};

/** Colored animated glyph for one phase (own cadence timer; frozen under
 * R4_TUI_STILL=1 — P4 motion budget). Exported for the status bar's work
 * indicator. */
export function PhaseGlyph(props: { phase: Exclude<AgentPhase, "idle"> }) {
  const style = () => PHASE_STYLE[props.phase];
  const [frame, setFrame] = createSignal(0);
  if (decorativeMotion()) {
    const timer = setInterval(() => {
      setFrame((f) => (f + 1) % style().frames.length);
    }, style().intervalMs);
    onCleanup(() => clearInterval(timer));
  }
  return <text fg={style().fg}>{style().frames[frame()]}</text>;
}

/** Status phrase per phase (elapsed seconds included via thinking.elapsedMs). */
export function phaseStatusText(phase: AgentPhase): string {
  const elapsed =
    activeTab().thinking.startedAtMs != null ? activeTab().thinking.elapsedMs / 1000 : 0;
  const timer = elapsed >= 1 ? ` ⏱ ${elapsed.toFixed(0)}s` : "";
  if (phase === "awaiting") {
    return `awaiting approval: ${activeTab().toolApproval?.tool ?? "tool"}`;
  }
  if (phase === "running") return `${activeTab().chatTurnStatus}…${timer}`;
  if (phase === "thinking") return `thinking…${timer}`;
  return "";
}

/** Wave-G: elapsed-second timer hue — statusInfo fresh, warn at 8s, err at 30s.
 * Honest pulse: only fires while actually streaming (thinking.elapsedMs > 0). */
export function pulseFgForElapsed(): string {
  const ms = activeTab().thinking.elapsedMs ?? 0;
  if (ms >= 30_000) return palette.statusErr;
  if (ms >= 8_000) return palette.statusWarn;
  return palette.statusInfo;
}

/**
 * Compose the status-bar work line: phase text (with elapsed timer) plus
 * turn volume context — completed tool calls and active/total agents — so
 * the one bottom indicator carries the TYPE of work (status text names the
 * running tool / phase) and the SCALE of it (tool count, agent count).
 * Pure: counts come in as arguments for unit tests.
 */
export function workStatusLine(
  phase: AgentPhase,
  counts: { tools: number; agentsActive: number; agentsTotal: number },
): string {
  let line = phaseStatusText(phase);
  if (counts.tools > 0) line += ` · tools ${counts.tools}`;
  if (counts.agentsTotal > 0) line += ` · agents ${counts.agentsActive}/${counts.agentsTotal}`;
  return line;
}
