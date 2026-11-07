/**
 * Explainability Panel — Every autonomous decision is explorable.
 *
 * OpenTUI: one `<text>` per row — sibling texts overpaint.
 */

import { For, Show, createMemo } from "solid-js";
import { agentRuntime } from "../stores/agent-runtime";
import { workspace, actions } from "../stores";
import { palette } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";
import { Panel } from "../ui/Panel";

/** Row cap tracks the real panel width (full bottom-row) — was fixed 56. */
const expCap = () => panePanelWidth(1, 8);
import type { DecisionExplanation } from "../types";

function confidenceColor(confidence: number): string {
  if (confidence >= 0.8) return palette.statusOk;
  if (confidence >= 0.5) return palette.statusWarn;
  return palette.statusErr;
}

function Line(props: { fg?: string; children: string; marginTop?: number }) {
  return (
    <box height={1} width="100%" overflow="hidden" marginTop={props.marginTop}>
      <text fg={props.fg ?? palette.fg}>{props.children}</text>
    </box>
  );
}

function ExplanationView(props: { explanation: DecisionExplanation }) {
  const exp = props.explanation;
  const confColor = () => confidenceColor(exp.confidenceCalculation.posterior);

  return (
    <Panel
      flexDirection="column"
      marginBottom={1}
      padding={1}
      overflow="hidden"
    >
      <Line fg={palette.accent}>
        {truncate(`Decision ${exp.decisionId.slice(0, 8)} · ${exp.decisionType}`, expCap())}
      </Line>
      <Line fg={palette.accent2}>{truncate(`Target: ${exp.target}`, expCap())}</Line>

      <Show when={exp.reasoningChain.length > 0}>
        <Line fg={palette.accent} marginTop={1}>
          Reasoning Chain:
        </Line>
        <For each={exp.reasoningChain}>
          {(step, i) => <Line>{truncate(`${i() + 1}. ${step}`, expCap())}</Line>}
        </For>
      </Show>

      <Show when={exp.supportingEvidence.length > 0}>
        <Line fg={palette.accent} marginTop={1}>
          Supporting Evidence:
        </Line>
        <For each={exp.supportingEvidence}>
          {(evidence) => (
            <Line fg={confidenceColor(evidence.confidence)}>
              {truncate(
                `${evidence.id} ${evidence.description} ${(evidence.confidence * 100).toFixed(0)}%`,
                expCap(),
              )}
            </Line>
          )}
        </For>
      </Show>

      <Panel
        flexDirection="column"
        marginTop={1}
        tone="raised"
        padding={1}
        overflow="hidden"
      >
        <Line fg={palette.accent}>Confidence:</Line>
        <Line fg={confColor()}>
          {truncate(
            `Prior ${exp.confidenceCalculation.prior.toFixed(2)} · Likelihood ${exp.confidenceCalculation.likelihood.toFixed(2)} · Posterior ${exp.confidenceCalculation.posterior.toFixed(2)}`,
            expCap(),
          )}
        </Line>
      </Panel>

      <Line fg={palette.fgDim} marginTop={1}>
        {truncate(`${exp.timestamp.slice(0, 19)} · ${exp.agentId}`, expCap())}
      </Line>
    </Panel>
  );
}

export function ExplainabilityPanel() {
  const explanations = createMemo(() => agentRuntime.explanations);
  const focused = () =>
    workspace.researchFocus === "explain" || workspace.researchBottomTab === "explain";
  const selected = createMemo(() => {
    const list = explanations();
    if (list.length === 0) return null;
    const id = workspace.selectedObjectId?.startsWith("explain:")
      ? workspace.selectedObjectId.slice(8)
      : null;
    return list.find((e) => e.decisionId === id) ?? list[0]!;
  });

  return (
    <Panel
      width="100%"
      height="100%"
      alignSelf="stretch"
      accent={focused() ? palette.borderFocus : palette.border}
      title="Explainability Mode"
      titleColor={palette.accent2}
      tone={focused() ? "raised" : "base"}
      flexDirection="column"
      padding={1}
      overflow="hidden"
    >
      <Show
        when={explanations().length > 0}
        fallback={
          <Line fg={palette.fgDim}>
            No decisions yet — Companion tools and RC1 jobs appear here.
          </Line>
        }
      >
        <Line fg={palette.fgDim}>
          {`↑↓ select decision · ${explanations().length} total · j Context JSON`}
        </Line>
        <scrollbox
          flexGrow={1}
          minHeight={0}
          stickyScroll
          stickyStart="top"
          scrollY
          overflow="hidden"
          width="100%"
        >
          <For each={explanations().slice(0, 6)}>
            {(exp) => {
              const active = () => selected()?.decisionId === exp.decisionId;
              return (
                <box
                  height={1}
                  width="100%"
                  overflow="hidden"
                  backgroundColor={active() ? palette.bgActive : undefined}
                  onMouseDown={() => actions.selectObject(`explain:${exp.decisionId}`)}
                >
                  <text fg={active() ? palette.accent : palette.fgDim}>
                    {truncate(
                      `${active() ? "›" : " "} ${exp.decisionType} · ${exp.target}`,
                      expCap(),
                    )}
                  </text>
                </box>
              );
            }}
          </For>
          <Show when={selected()}>
            <ExplanationView explanation={selected()!} />
          </Show>
        </scrollbox>
      </Show>
    </Panel>
  );
}
