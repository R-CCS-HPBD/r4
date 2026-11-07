/**
 * Status-chip (V2/G4 wave).
 *
 * Tiny single-purpose chip that packs one status datum (label + state hue)
 * inside the same box style at every site it renders, making the status
 * line's group boundaries visible instead of one long terminal string.
 * Semantics only — no new palette entries, no new chrome rules.
 */

import { palette } from "../themes";

import type { ChipState } from "../lib/chip-state";

export type { ChipState } from "../lib/chip-state";

export function StatusChip(props: { label: string; state: ChipState }) {
  const fg = () => {
    if (props.state === "ok") return palette.statusOk;
    if (props.state === "warn") return palette.statusWarn;
    if (props.state === "error") return palette.statusErr;
    if (props.state === "info") return palette.fgBright ?? palette.fg;
    return palette.fgDim;
  };
  return (
    <box height={1} overflow="hidden" paddingLeft={1} paddingRight={1}>
      <text fg={fg()}>{props.label}</text>
    </box>
  );
}
