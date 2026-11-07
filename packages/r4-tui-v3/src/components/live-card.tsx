/**
 * Dashboard live metric card.
 */

import { Sparkline } from "./sparkline";
import { palette } from "../themes";
import { t, fg, bold, dim } from "@opentui/core";
import { Panel } from "../ui/Panel";
import { StyledTextView } from "../ui/StyledTextView";

export function LiveCard(props: {
  label: string;
  value: string | number;
  color?: string;
  sparkline?: number[];
}) {
  return (
    <Panel
      width={22}
      height={props.sparkline ? 11 : 7}
      accent={props.color ?? palette.border}
      tone="raised"
      flexDirection="column"
      padding={1}
    >
      <StyledTextView text={t`${dim(props.label)}`} />
      <StyledTextView text={t`${bold(fg(props.color ?? palette.accent)(String(props.value)))}`} />
      {props.sparkline && <Sparkline data={props.sparkline} height={4} color={props.color} />}
    </Panel>
  );
}
