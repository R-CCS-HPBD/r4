/**
 * Loop Route — Phase 9 — Research DAG (local compositional demo).
 *
 * Single-string rows avoid OpenTUI multi-<text> overwrite glitches.
 */

import { For, Show, createMemo, createSignal } from "solid-js";
import { workflowStore, workflowActions, getStandardStepTemplates, createStepBlockReason, CREATE_STEP_NOT_DEMO, CREATE_STEP_FULL } from "../stores/workflow";
import { loopStore, loopActions } from "../stores/autonomous-loop";
import { actions, workspace } from "../stores";
import { isDemoVisuals } from "../lib/console-mode";
import { localTimeHHMMSS } from "../lib/time";
import { LOOP_EMPTY } from "../lib/empty-hints";
import { KeyTrap } from "../components/key-trap";
import { palette, statusColor, statusIcon } from "../themes";
import { truncate } from "../lib/text";
import { panePanelWidth } from "../lib/term-width";

/** Row cap tracks the real pane width — was fixed 36/40/42. */
const loopCap = () => panePanelWidth(1, 6);
import { t, bold } from "@opentui/core";
import type { WorkflowStepType } from "../stores/workflow";
import type { KeyEvent } from "@opentui/core";
import { Panel } from "../ui/Panel";

export function formatTemplateTypes(types: string[]): string {
  return types.join(" → ");
}

export function formatStepLine(
  index: number,
  step: { status: string; label: string; type: string; errors: string[] },
): string {
  const gate = step.type === "gate" ? " [GATE]" : "";
  const errs = step.errors.length > 0 ? ` [!${step.errors.length}]` : "";
  return `${statusIcon(step.status)} #${index} ${step.label} (${step.type})${gate}${errs}`;
}

// ── Step Template Cards ────────────────────────────────────────────────

function StepTemplateCard(props: { template: { name: string; description: string; types: string[] } }) {
  const [error, setError] = createSignal("");
  const handleAdd = () => {
    const result = workflowActions.addStepsFromTemplate({
      name: props.template.name,
      types: props.template.types as WorkflowStepType[],
    });
    setError(result.errors[0] ?? "");
  };

  return (
    <Panel
      flexDirection="column"
      accent={palette.knowledge}
      padding={1}
      marginBottom={1}
      overflow="hidden"
      width="100%"
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.accent}>{truncate(props.template.name, loopCap())}</text>
      </box>
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.fgDim}>{truncate(formatTemplateTypes(props.template.types), loopCap())}</text>
      </box>
      <box height={1} width="100%" overflow="hidden" marginTop={1}>
        <text fg={palette.statusOk} onMouseDown={handleAdd}>
          [+] Add
        </text>
      </box>
      <Show when={error()}>
        <box height={1} width="100%" overflow="hidden">
          <text fg={palette.statusErr}>{truncate(error(), 36)}</text>
        </box>
      </Show>
    </Panel>
  );
}

// ── Step Editor ─────────────────────────────────────────────────────────

function StepEditor() {
  const [stepType, setStepType] = createSignal<WorkflowStepType>("ingest");
  const [manualError, setManualError] = createSignal("");

  const stepTypes: WorkflowStepType[] = [
    "ingest",
    "extract",
    "hypothesis",
    "experiment",
    "analysis",
    "review",
    "publication",
    "gate",
  ];

  const handleAdd = () => {
    const step = workflowActions.createStep(stepType());
    if (step) {
      setManualError("");
      return;
    }
    const why = createStepBlockReason();
    if (why === "not_demo") setManualError(CREATE_STEP_NOT_DEMO);
    else if (why === "full") setManualError(CREATE_STEP_FULL(workflowStore.maxSteps));
    else setManualError(CREATE_STEP_FULL(workflowStore.maxSteps));
  };

  return (
    <Panel
      flexDirection="column"
      width="100%"
      tone="raised"
      padding={1}
      overflow="hidden"
    >
      <text fg={palette.accent}>{t`${bold("Manual Step")}`}</text>
      <text fg={palette.fgDim} marginTop={1}>
        Type: {stepType()}
      </text>
      <box flexDirection="column" gap={0} marginTop={1} overflow="hidden">
        <For each={stepTypes}>
          {(type) => (
            <box
              height={1}
              width="100%"
              overflow="hidden"
              backgroundColor={stepType() === type ? palette.bgActive : undefined}
              onMouseDown={() => setStepType(type)}
            >
              <text fg={stepType() === type ? palette.accent : palette.fgDim}>
                {stepType() === type ? `› ${type}` : `  ${type}`}
              </text>
            </box>
          )}
        </For>
      </box>
      <box height={1} marginTop={1} overflow="hidden">
        <text fg={palette.statusOk} onMouseDown={handleAdd}>
          [+] Add Step
        </text>
      </box>
      <Show when={manualError()}>
        <text fg={palette.statusErr} marginTop={1}>
          {manualError()}
        </text>
      </Show>
    </Panel>
  );
}

// ── Step List with Inline Errors ────────────────────────────────────────

function StepListItem(props: { step: (typeof workflowStore.steps)[number]; index: number }) {
  const [showError, setShowError] = createSignal(false);
  const lineColor = () => {
    switch (props.step.status) {
      case "completed":
        return palette.statusOk;
      case "running":
        return palette.statusInfo;
      case "failed":
      case "blocked":
        return palette.statusErr;
      case "waiting":
        return palette.statusWarn;
      default:
        return palette.fg;
    }
  };

  return (
    <box flexDirection="column" marginBottom={0} overflow="hidden" width="100%">
      <box
        height={1}
        width="100%"
        overflow="hidden"
        onMouseDown={() => {
          if (props.step.errors.length > 0) setShowError(!showError());
        }}
      >
        <text fg={lineColor()}>{truncate(formatStepLine(props.index, props.step), 42)}</text>
      </box>
      <Show when={showError() && props.step.errors.length > 0}>
        <For each={props.step.errors}>
          {(error) => (
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.statusErr}>{truncate(`  • ${error}`, loopCap())}</text>
            </box>
          )}
        </For>
        <text
          fg={palette.accent}
          onMouseDown={() => workflowActions.clearStepErrors(props.step.id)}
        >
          [Clear errors]
        </text>
        <text fg={palette.fgDim} onMouseDown={() => workflowActions.removeStep(props.step.id)}>
          [× Remove]
        </text>
      </Show>
    </box>
  );
}

// ── Workflow Controls ──────────────────────────────────────────────────

function WorkflowControls() {
  const handleAssemble = () => {
    const result = workflowActions.assemble();
    if (!result.valid && result.errors.length > 0) {
      actions.sendSystemNotice(`Workflow assembly failed: ${result.errors.join("; ")}`);
    }
  };

  return (
    <box flexDirection="column" gap={0} overflow="hidden">
      <Show when={isDemoVisuals()}>
        <box flexDirection="row" gap={2} height={1} overflow="hidden">
          <text
            fg={workflowStore.assembled ? palette.statusOk : palette.fgDim}
            onMouseDown={handleAssemble}
          >
            [Assemble]
          </text>
          <text
            fg={workflowStore.running ? palette.statusWarn : palette.statusInfo}
            onMouseDown={() => workflowActions.runWorkflow()}
          >
            [Run]
          </text>
          <text
            fg={palette.statusErr}
            onMouseDown={() => {
              workflowActions.reset();
              loopActions.stopLoop();
            }}
          >
            [Reset]
          </text>
        </box>
      </Show>
      <Show when={workflowStore.assembled}>
        <text fg={palette.statusOk}>✓ Assembled</text>
      </Show>
      <Show when={workflowStore.running}>
        <text fg={palette.statusInfo}>⟳ Running…</text>
      </Show>
      <Show when={workflowStore.validationErrors.length > 0}>
        <text fg={palette.statusErr}>
          {truncate(`Errors: ${workflowStore.validationErrors[0] ?? ""}`, loopCap())}
        </text>
      </Show>
    </box>
  );
}

// ── Gate Approval Panel ────────────────────────────────────────────────

function GateApprovalPanel() {
  const gate = createMemo(() => workflowStore.pendingGate);

  const handleApprove = () => {
    const g = gate();
    if (g) {
      workflowActions.approveGate(g.id);
      actions.sendSystemNotice("Gate approved — workflow continues.");
    }
  };

  const handleReject = () => {
    const g = gate();
    if (g) {
      workflowActions.rejectGate(g.id);
      actions.sendSystemNotice("Gate rejected — workflow stopped.");
    }
  };

  return (
    <Panel
      flexDirection="column"
      accent={palette.statusWarn}
      padding={1}
      overflow="hidden"
    >
      <text fg={palette.statusWarn}>{t`${bold("⚠ GATE: Waiting for approval")}`}</text>
      <Show when={gate()}>
        <text fg={palette.fg} marginTop={1}>
          {truncate(gate()!.label, 48)}
        </text>
        <text fg={palette.fgDim}>{truncate(gate()!.gateTitle ?? "Approve to continue", 48)}</text>
      </Show>
      <Show when={isDemoVisuals()}>
        <box flexDirection="row" gap={2} height={1} marginTop={1} overflow="hidden">
          <text fg={palette.statusOk} onMouseDown={handleApprove}>
            [A]pprove
          </text>
          <text fg={palette.statusErr} onMouseDown={handleReject}>
            [R]eject
          </text>
        </box>
      </Show>
    </Panel>
  );
}

// ── Loop History ────────────────────────────────────────────────────────

function LoopHistory() {
  const history = createMemo(() => loopStore.history.slice(0, 8));

  return (
    <box flexDirection="column" flexGrow={1} overflow="hidden" marginTop={1}>
      <text fg={palette.fgDim}>{t`${bold("Loop History")}`}</text>
      <For each={history()}>
        {(h) => (
          <box height={1} width="100%" overflow="hidden">
            <text fg={statusColor(h.phase)}>
              {truncate(
                `#${h.iteration} ${localTimeHHMMSS(h.timestamp)} ${h.phase}${h.error ? ` · ${h.error}` : ""}`,
                40,
              )}
            </text>
          </box>
        )}
      </For>
      <Show when={history().length === 0}>
        <text fg={palette.fgDim}>No loop history yet.</text>
      </Show>
    </box>
  );
}

// ── Main Route ──────────────────────────────────────────────────────────

export function LoopRoute() {
  const templates = getStandardStepTemplates();
  const steps = createMemo(() => workflowStore.steps);
  const demoVisuals = createMemo(() => isDemoVisuals());

  const handleKey = (event: KeyEvent) => {
    if (!demoVisuals()) return;
    if (event.name === "a" && event.ctrl) workflowActions.assemble();
    else if (event.name === "r" && event.ctrl) workflowActions.runWorkflow();
    else if (event.name === "c") workflowActions.reset();
  };

  return (
    <box
      flexDirection="column"
      width="100%"
      height="100%"
      gap={1}
      padding={1}
      overflow="hidden"
      onKeyDown={handleKey}
    >
      <box height={1} width="100%" overflow="hidden">
        <text fg={palette.accent}>
          {truncate(
            demoVisuals()
              ? `Workflow builder (local) · Steps ${steps().length}/${workflowStore.maxSteps}${
                  workflowStore.assembled ? " · Assembled" : ""
                }${workflowStore.running ? " · Running" : ""}`
              : "Workflow builder (local demo)",
            72,
          )}
        </text>
      </box>

      <Show
        when={demoVisuals()}
        fallback={
          <Panel
            flexDirection="column"
            flexGrow={1}
            padding={1}
            overflow="hidden"
          >
            <box height={1} width="100%" overflow="hidden">
              <text fg={palette.statusWarn}>Local compositional demo — not RC1</text>
            </box>
            <box height={2} width="100%" overflow="hidden" marginTop={1}>
              <text fg={palette.fg}>{LOOP_EMPTY}</text>
            </box>
            <box height={1} width="100%" overflow="hidden" marginTop={1}>
              <text fg={palette.fgDim}>
                Or restart with: r4 console --demo · open this space with F6
              </text>
            </box>
            <box
              height={1}
              width="100%"
              overflow="hidden"
              marginTop={1}
              onMouseDown={() => actions.setSpace("research")}
            >
              <text fg={palette.accent}>[Enter] Open Research (or F2)</text>
            </box>
            <KeyTrap
              paneScoped
              focused={workspace.paneView === "loop"}
              captureText
              onKeyDown={(event: KeyEvent) => {
                if (event.name === "return" || event.name === "enter") {
                  event.preventDefault();
                  actions.setSpace("research");
                }
              }}
            />
          </Panel>
        }
      >
        <box flexDirection="row" gap={1} flexGrow={1} overflow="hidden">
          <box flexDirection="column" width="30%" overflow="hidden">
            <text fg={palette.knowledge}>{t`${bold("Templates")}`}</text>
            <scrollbox flexGrow={1} stickyScroll stickyStart="top" scrollY overflow="hidden" marginTop={1}>
              <For each={templates}>{(template) => <StepTemplateCard template={template} />}</For>
            </scrollbox>
          </box>

          <box flexDirection="column" width="40%" overflow="hidden">
            <StepEditor />
            <text fg={palette.accent} marginTop={1}>
              {t`${bold("Steps")} (${String(steps().length)})`}
            </text>
            <box flexDirection="column" overflow="hidden" flexGrow={1} marginTop={1}>
              <For each={steps()}>
                {(step, i) => <StepListItem step={step} index={i() + 1} />}
              </For>
              <Show when={steps().length === 0}>
                <text fg={palette.fgDim}>No steps yet. Add from a template or manually.</text>
              </Show>
            </box>
          </box>

          <box flexDirection="column" width="30%" overflow="hidden">
            <WorkflowControls />
            <LoopHistory />
          </box>
        </box>

        <Show when={workflowStore.pendingGate}>
          <GateApprovalPanel />
        </Show>

        <text fg={palette.fgDim}>Ctrl+A Assemble · Ctrl+R Run · A/R gate</text>
      </Show>
    </box>
  );
}
