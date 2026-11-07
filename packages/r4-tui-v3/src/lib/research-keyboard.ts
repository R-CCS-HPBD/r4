/**
 * Keyboard-only Research space actions (no mouse / panel focus required).
 * Gated by space=research, bottom tab, and letterShortcutsBlocked().
 */

import { workspace, actions, activeTab } from "../stores";
import { answerToolApprovalGate } from "../bridge/approval-bridge";
import { agentRuntime, agentActions } from "../stores/agent-runtime";
import { strategyActions } from "../stores/research-strategy";
import { blackboardActions } from "../stores/scientific-blackboard";
import { loopActions } from "../stores/autonomous-loop";
import { isDemoVisuals } from "./console-mode";
import {
  isReadinessGate,
  projectIdFromReadinessGate,
} from "./readiness-sync";
import { letterShortcutsBlocked, askAboutSelectedResearchObject } from "./space-shortcuts";
import {
  buildProjectGraph,
  graphCursorIndex,
} from "../components/knowledge-graph-viewer";

export type ResearchFocus = "graph" | "agents" | "verify" | "approvals" | "strategy" | "explain";

export const RESEARCH_FOCUS_ORDER: ResearchFocus[] = [
  "graph",
  "agents",
  "verify",
  "approvals",
  "strategy",
  "explain",
];

function pendingApprovals() {
  return agentRuntime.approvalGates.filter((g) => g.status === "pending");
}

function allAgents() {
  return agentRuntime.agents;
}

function workflowNodes() {
  return agentRuntime.workflowGraph.nodes;
}

export function cycleResearchFocus(dir: 1 | -1): boolean {
  if (workspace.paneView !== "research") return false;
  // Overlays own keys — do not Tab-cycle panes underneath (shrinks layout).
  if (letterShortcutsBlocked() && !workspace.verifyInputFocused) return false;
  // Tab may leave Live verify (Esc also leaves). Letter keys are gated in the keymap.
  if (workspace.verifyInputFocused) {
    actions.setVerifyInputFocused(false);
  }
  const order = RESEARCH_FOCUS_ORDER;
  const cur = workspace.researchFocus ?? "graph";
  const idx = Math.max(0, order.indexOf(cur as ResearchFocus));
  const next = order[(idx + dir + order.length) % order.length]!;
  actions.setResearchFocus(next);
  if (next === "verify") {
    actions.setVerifyInputFocused(true);
  } else {
    actions.setVerifyInputFocused(false);
  }
  // Bottom tabs follow pane focus so a leftover Explain/Strategy tab cannot
  // steal KeyTrap / ↑↓ after Tab wraps back to graph/agents/verify.
  if (next === "approvals") {
    const pending = pendingApprovals();
    if (pending[0] && !pending.some((g) => g.id === workspace.selectedApprovalId)) {
      actions.setSelectedApprovalId(pending[0].id);
    }
    actions.setResearchBottomTab("ops");
  } else if (next === "strategy") {
    actions.setResearchBottomTab("strategy");
  } else if (next === "explain") {
    actions.setResearchBottomTab("explain");
  } else if (next === "graph" || next === "agents" || next === "verify") {
    // Middle-row panes: restore Ops so Explain does not keep owning nav.
    if (workspace.researchBottomTab === "explain" || workspace.researchBottomTab === "strategy") {
      actions.setResearchBottomTab("ops");
    }
  }
  actions.setSpaceFeedback(
    next === "graph"
      ? "Focus: Project graph · ↑↓ · Enter ask · k Knowledge"
      : next === "agents"
        ? "Focus: Agents · ↑↓ select"
        : next === "verify"
          ? "Focus: Live verify · type · Enter · Esc"
          : next === "strategy"
            ? "Focus: Strategy · alt+shift+2 · n/e rank/eval (--demo)"
            : next === "explain"
              ? "Focus: Explain · ↑↓ select decision · j Context JSON"
              : "Focus: Approvals · ↑↓ · y approve · n reject",
  );
  return true;
}

export function focusLiveVerify(): boolean {
  if (workspace.paneView !== "research") return false;
  // Already typing in verify — never steal letter keys (v, a, r, …).
  if (workspace.verifyInputFocused || letterShortcutsBlocked()) return false;
  actions.setResearchFocus("verify");
  actions.setVerifyInputFocused(true);
  actions.setSpaceFeedback("Live verify focused · Esc to leave");
  return true;
}

function selectedOrFirstApproval() {
  const pending = pendingApprovals();
  if (pending.length === 0) return null;
  const id = workspace.selectedApprovalId;
  return pending.find((g) => g.id === id) ?? pending[0]!;
}

export function cycleApprovalSelection(dir: 1 | -1): boolean {
  const pending = pendingApprovals();
  if (pending.length === 0) {
    actions.sendSystemNotice("No pending approvals — /project readiness");
    return true;
  }
  const curId = workspace.selectedApprovalId;
  let idx = pending.findIndex((g) => g.id === curId);
  if (idx < 0) idx = 0;
  else idx = (idx + dir + pending.length) % pending.length;
  const gate = pending[idx]!;
  actions.setSelectedApprovalId(gate.id);
  actions.setSpaceFeedback(`Approval: ${gate.title.slice(0, 40)}`);
  return true;
}

/** A tool-approval gate's mirror answers the companion card (or names the
 * background tab holding it) instead of only the local gate (r2 #13). */
function answeredAsToolCard(gateId: string, decision: "yes" | "no"): boolean {
  if (!answerToolApprovalGate(gateId, decision)) return false;
  agentActions.refreshMissionControl();
  actions.setSelectedApprovalId(null);
  return true;
}

function approveGate(): boolean {
  const gate = selectedOrFirstApproval();
  if (!gate) {
    actions.sendSystemNotice("No pending approvals");
    return true;
  }
  if (answeredAsToolCard(gate.id, "yes")) return true;
  if (isReadinessGate(gate.objectId)) {
    const fromGate = projectIdFromReadinessGate(gate.objectId);
    const projectId = fromGate ?? activeTab().project?.id;
    if (projectId) {
      void actions.dispatchSlashFromChat(
        `/project run ${projectId} --simulate --approve --max-iterations 2`,
      );
    }
  }
  agentActions.resolveApproval(gate.id, "approved");
  agentActions.refreshMissionControl();
  actions.setSelectedApprovalId(null);
  actions.sendSystemNotice(`[approval] approved: ${gate.title}`);
  return true;
}

function rejectGate(): boolean {
  const gate = selectedOrFirstApproval();
  if (!gate) {
    actions.sendSystemNotice("No pending approvals");
    return true;
  }
  if (answeredAsToolCard(gate.id, "no")) return true;
  agentActions.resolveApproval(gate.id, "rejected");
  agentActions.refreshMissionControl();
  actions.setSelectedApprovalId(null);
  actions.sendSystemNotice(`[approval] rejected: ${gate.title}`);
  return true;
}

type SideItem = { kind: "agent" | "wf"; id: string };

function sidePanelItems(): SideItem[] {
  return [
    ...allAgents().map((a) => ({ kind: "agent" as const, id: a.id })),
    ...workflowNodes().map((n) => ({ kind: "wf" as const, id: n.id })),
  ];
}

/** Agents then workflow nodes — one ↑↓ list for keyboard-only side selection. */
export function cycleAgentSelection(dir: 1 | -1): boolean {
  const items = sidePanelItems();
  if (items.length === 0) {
    actions.sendSystemNotice("No agents/workflow yet — [R]un or Companion chat");
    return true;
  }
  const cur = workspace.selectedObjectId;
  let idx = items.findIndex((it) =>
    it.kind === "agent" ? cur === `agent:${it.id}` : cur === `wf:${it.id}`,
  );
  if (idx < 0) idx = dir > 0 ? -1 : 0;
  idx = (idx + dir + items.length) % items.length;
  const item = items[idx]!;
  if (item.kind === "agent") {
    actions.selectObject(`agent:${item.id}`);
  } else {
    agentActions.selectWorkflowNode(item.id);
    actions.selectObject(`wf:${item.id}`);
  }
  return true;
}

export function cycleWorkflowSelection(dir: 1 | -1): boolean {
  return cycleAgentSelection(dir);
}

export function cycleExplainSelection(dir: 1 | -1): boolean {
  const list = agentRuntime.explanations;
  if (list.length === 0) {
    actions.sendSystemNotice("No decisions yet — Companion tools / RC1 jobs");
    return true;
  }
  const cur = workspace.selectedObjectId?.startsWith("explain:")
    ? workspace.selectedObjectId.slice(8)
    : null;
  let idx = list.findIndex((e) => e.decisionId === cur);
  if (idx < 0) idx = dir > 0 ? -1 : 0;
  idx = (idx + dir + list.length) % list.length;
  actions.selectObject(`explain:${list[idx]!.decisionId}`);
  return true;
}

/** Strategy-tab demo keys (alt+shift+2). n/e strategy · w blackboard · i/x/o loop (o = iter; avoid Mission r). */
export function handleStrategyDemoKey(key: string): boolean {
  if (workspace.paneView !== "research") return false;
  if (workspace.researchBottomTab !== "strategy") return false;
  if (!isDemoVisuals()) return false;
  if (letterShortcutsBlocked()) return false;

  if (key === "n") {
    strategyActions.rankActions();
    actions.setSpaceFeedback("Strategy: ranked actions");
    return true;
  }
  if (key === "e") {
    strategyActions.evaluateStrategy();
    actions.setSpaceFeedback("Strategy: evaluated");
    return true;
  }
  if (key === "w") {
    blackboardActions.writeEntry({
      type: "observation",
      title: "Test observation from TUI",
      content: "This is a test observation written via the Blackboard panel.",
      authorRole: "literature",
      tags: ["test", "tui"],
    });
    actions.setSpaceFeedback("Blackboard: wrote observation");
    return true;
  }
  if (key === "i") {
    loopActions.startLoop();
    actions.setSpaceFeedback("Loop: started");
    return true;
  }
  if (key === "x") {
    loopActions.stopLoop();
    actions.setSpaceFeedback("Loop: stopped");
    return true;
  }
  if (key === "o") {
    loopActions.runIteration();
    actions.setSpaceFeedback("Loop: iteration");
    return true;
  }
  return false;
}

/** Ops-tab approval keys (alt+shift+1). */
export function handleOpsApprovalKey(key: string): boolean {
  if (workspace.paneView !== "research") return false;
  if (workspace.researchBottomTab !== "ops") return false;
  if (letterShortcutsBlocked()) return false;
  // Prefer when focus is approvals, or always allow y/n on ops for keyboard-only.
  if (key === "y") return approveGate();
  if (key === "n") return rejectGate();
  if (key === "]" || key === "[") {
    return cycleApprovalSelection(key === "]" ? 1 : -1);
  }
  return false;
}

export function cycleGraphSelection(dir: 1 | -1): boolean {
  const nodes = buildProjectGraph().nodes;
  if (nodes.length === 0) {
    actions.sendSystemNotice("Graph empty — /project import → init → plan");
    return true;
  }
  const base = graphCursorIndex(nodes, workspace.selectedObjectId);
  const next =
    dir < 0 ? Math.max(0, base - 1) : Math.min(nodes.length - 1, base + 1);
  actions.selectObject(nodes[next]!.id);
  return true;
}

/**
 * ↑↓ while Research has pane focus (graph / agents / approvals / explain).
 * Bound globally so KeyTrap focus is not required for keyboard-only use.
 */
export function handleResearchFocusNav(dir: 1 | -1): boolean {
  if (workspace.paneView !== "research") return false;
  if (workspace.verifyInputFocused) return false;
  if (letterShortcutsBlocked()) return false;
  const focus = workspace.researchFocus ?? "graph";
  // Prefer Tab focus over leftover bottom-tab state.
  if (focus === "explain") return cycleExplainSelection(dir);
  if (focus === "agents") return cycleAgentSelection(dir);
  if (focus === "approvals") return cycleApprovalSelection(dir);
  if (focus === "graph") return cycleGraphSelection(dir);
  return false;
}

/** True when Project graph KeyTrap should own ↑↓. */
export function researchGraphKeyTrapFocused(): boolean {
  if (workspace.paneView !== "research") return false;
  if (workspace.verifyInputFocused) return false;
  if (workspace.researchBottomTab === "explain") return false;
  if (workspace.researchFocus === "explain") return false;
  return (workspace.researchFocus ?? "graph") === "graph";
}

export function handleResearchContextJsonToggle(): boolean {
  if (workspace.paneView !== "research") return false;
  if (letterShortcutsBlocked()) return false;
  if (!workspace.contextPanelOpen) actions.toggleContextPanel();
  const next = workspace.contextPanelTab === "json" ? "overview" : "json";
  actions.setContextPanelTab(next);
  actions.setSpaceFeedback(
    next === "json" ? "Context: JSON" : "Context: Details",
  );
  return true;
}

/** Re-export ask for tests. */
export { askAboutSelectedResearchObject };
