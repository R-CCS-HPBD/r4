/**
 * R4 mascot — animated ASCII character that reacts to platform state.
 */

import { For, createMemo, createSignal, onCleanup } from "solid-js";
import { workspace, activeTab } from "../stores";
import { palette } from "../themes";
import { t, fg } from "@opentui/core";
import { StyledTextView } from "../ui/StyledTextView";

type MascotState = "idle" | "listening" | "thinking" | "running" | "success" | "error";

const frames: Record<MascotState, string[][]> = {
  idle: [
    ["   ╭─────╮   ", "  ╱ o   o ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
    ["   ╭─────╮   ", "  ╱ o   o ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
  ],
  listening: [
    ["   ╭─────╮   ", "  ╱ ○   ○ ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳─╳─┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
    ["   ╭─────╮   ", "  ╱ ◉   ◉ ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳─│─┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
  ],
  thinking: [
    ["   ╭─────╮   ", "  ╱ ◠   ◠ ╲  ", " │    ◡    │ ", " │ ╭─⚙─⚙─╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
    ["   ╭─────╮   ", "  ╱ ◠   ◠ ╲  ", " │    ◡    │ ", " │ ╭─⚙─⚙─╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
  ],
  running: [
    ["   ╭─────╮   ", "  ╱ o   o ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳───┳━╯  ", "  ╱┃   ┃   ", " ━┛   ┗━   "],
    ["   ╭─────╮   ", "  ╱ o   o ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃╲  ", "   ━━━┛ ┗━ "],
  ],
  success: [
    ["   ╭─────╮   ", "  ╱ ^   ^ ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳✔─┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
    ["   ╭─────╮   ", "  ╱ ✨ ✨ ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
  ],
  error: [
    ["   ╭─────╮   ", "  ╱ ✖   ✖ ╲  ", " │    ◡    │ ", " │ ╭─────╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
    ["   ╭─────╮   ", "  ╱ ✖   ✖ ╲  ", " │    ◠    │ ", " │ ╭─────╮ │ ", "  ╰┳───┳━╯  ", "   ┃   ┃   ", "   ┗━━━┛   "],
  ],
};

const stateColor: Record<MascotState, string> = {
  idle: palette.fgBright,
  listening: palette.accent,
  thinking: palette.knowledge,
  running: palette.statusInfo,
  success: palette.statusOk,
  error: palette.statusErr,
};

export function Mascot() {
  const [frame, setFrame] = createSignal(0);

  const state = createMemo<MascotState>(() => {
    const activeAgents = workspace.agents.filter((a) => a.status !== "idle");
    // Demo/offline banners are expected — do not show "dead" X-eyes for them.
    const err = workspace.backendError ?? "";
    const expectedOffline =
      workspace.demoMode ||
      err.startsWith("DEMO MODE") ||
      err.startsWith("OFFLINE FALLBACK");
    const hasError =
      workspace.agents.some((a) => a.status === "error") ||
      (Boolean(err) && !expectedOffline);
    const isRunning = activeAgents.some((a) => a.status === "executing");
    const isThinking = activeAgents.some((a) => a.status === "thinking");

    if (hasError) return "error";
    // Live companion turn state: tool execution beats model streaming.
    if (activeTab().chatTurnStatus) return "running";
    if (activeTab().chatStreaming || activeTab().thinking.open) return "thinking";
    if (workspace.mascotState === "success" || workspace.mascotState === "listening") return workspace.mascotState;
    if (isRunning) return "running";
    if (isThinking) return "thinking";
    return "idle";
  });

  const timer = setInterval(() => {
    setFrame((f) => (f + 1) % frames[state()].length);
  }, 600);
  onCleanup(() => clearInterval(timer));

  const lines = () => frames[state()][frame() % frames[state()].length];
  const color = () => stateColor[state()];

  return (
    <box flexDirection="column" alignItems="center" padding={1}>
      <For each={lines()}>
        {(line) => <StyledTextView text={t`${fg(color())(line)}`} />}
      </For>
    </box>
  );
}
