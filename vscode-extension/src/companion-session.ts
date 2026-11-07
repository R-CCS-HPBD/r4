/**
 * Companion session state — mirrors r4 companion modes.
 *
 * Persists interaction mode, plan state, and tool policy across chat turns.
 * Mirrors the Python companion session's metadata-based state management.
 *
 * Modes:
 *   chat   — Normal mode. Agent uses tools freely (permission mode: TRUST).
 *   plan   — Planning only. No tools executed; returns structured plan.
 *   autopilot — Agent executes with auto-approved tools (same as chat but
 *              makes the intent explicit to the model).
 *
 * Re-exports from shared-constants.ts for backward compatibility.
 */

import * as vscode from "vscode";

// Import from shared-constants for use in this module.
import {
  buildR4SystemPrompt as _buildR4SystemPrompt,
  KNOWN_R4_TOOLS as _KNOWN_R4_TOOLS,
  PLAN_MODE_SYSTEM_PROMPT as _PLAN_MODE_SYSTEM_PROMPT,
  PLAN_MODE_FOOTER as _PLAN_MODE_FOOTER,
  PLAN_CONFIRM_RE as _PLAN_CONFIRM_RE,
  PLAN_CANCEL_RE as _PLAN_CANCEL_RE,
  buildPlanExecutionPrompt as _buildPlanExecutionPrompt,
  buildPlanRevisionPrompt as _buildPlanRevisionPrompt,
} from "./shared-constants";

// Re-export from shared-constants for backward compatibility.
export {
  _buildR4SystemPrompt as buildR4SystemPrompt,
  _KNOWN_R4_TOOLS as KNOWN_R4_TOOLS,
  _PLAN_MODE_SYSTEM_PROMPT as PLAN_MODE_SYSTEM_PROMPT,
  _PLAN_MODE_FOOTER as PLAN_MODE_FOOTER,
  _PLAN_CONFIRM_RE as PLAN_CONFIRM_RE,
  _PLAN_CANCEL_RE as PLAN_CANCEL_RE,
  _buildPlanExecutionPrompt as buildPlanExecutionPrompt,
  _buildPlanRevisionPrompt as buildPlanRevisionPrompt,
};

export type InteractionMode = "chat" | "plan" | "autopilot";

export interface PlanState {
  awaiting_confirmation: boolean;
  pending_request: string;
}

export interface CompanionSessionState {
  interaction_mode: InteractionMode;
  plan_mode_state: PlanState | null;
  /** Whether to auto-approve tools in chat mode. Mirrors PermissionMode.TRUST. */
  auto_approve_tools: boolean;
  /** System prompt override for companion capability mode. */
  companion_capability?: string;
}

const DEFAULT_STATE: CompanionSessionState = {
  interaction_mode: "chat",
  plan_mode_state: null,
  auto_approve_tools: false,
  companion_capability: undefined,
};

/** Read persisted state from memento, falling back to defaults. */
export function loadSessionState(memento: {
  get: <T>(key: string, fallback?: T) => T | undefined;
}): CompanionSessionState {
  const stored = memento.get<CompanionSessionState>("r4_companion_state");
  if (stored && typeof stored === "object") {
    return {
      ...DEFAULT_STATE,
      interaction_mode: (stored.interaction_mode as InteractionMode) ?? "chat",
      plan_mode_state: stored.plan_mode_state ?? null,
      auto_approve_tools: stored.auto_approve_tools ?? false,
      companion_capability: stored.companion_capability,
    };
  }
  return { ...DEFAULT_STATE };
}

/** Persist session state to memento. */
export function saveSessionState(
  memento: {
    update: (key: string, value: CompanionSessionState) => Thenable<void>;
  },
  state: CompanionSessionState,
): void {
  void memento.update("r4_companion_state", state);
}

// ── Mode helpers ───────────────────────────────────────────────

/** Change interaction mode; clears plan state when leaving plan mode. */
export function setMode(
  state: CompanionSessionState,
  mode: InteractionMode,
): CompanionSessionState {
  const copy = { ...state, interaction_mode: mode };
  if (mode !== "plan") {
    copy.plan_mode_state = null;
  }
  return copy;
}

/** Set plan mode; clear auto-approve so the user must confirm. */
export function setPlanMode(state: CompanionSessionState): CompanionSessionState {
  return setMode(state, "plan");
}

/** Enable autopilot mode (chat with auto-approve). */
export function setAutopilotMode(state: CompanionSessionState): CompanionSessionState {
  return { ...setMode(state, "autopilot"), auto_approve_tools: true };
}

/** Toggle between chat and autopilot (same as toggle tool auto-approve). */
export function toggleAutoApprove(state: CompanionSessionState): CompanionSessionState {
  return { ...state, auto_approve_tools: !state.auto_approve_tools };
}

// ── Plan mode helpers ──────────────────────────────────────────

/** Set plan state (awaiting confirmation). */
export function setPlanAwaiting(
  state: CompanionSessionState,
  pendingRequest: string,
): CompanionSessionState {
  return {
    ...state,
    plan_mode_state: { awaiting_confirmation: true, pending_request: pendingRequest },
  };
}

/** Clear plan state. */
export function clearPlanState(state: CompanionSessionState): CompanionSessionState {
  return { ...state, plan_mode_state: null };
}

/** Check if a user message confirms the pending plan. */
export function isPlanConfirmation(message: string): boolean {
  return _PLAN_CONFIRM_RE.test(message);
}

/** Check if a user message cancels the pending plan. */
export function isPlanCancellation(message: string): boolean {
  return _PLAN_CANCEL_RE.test(message);
}

/** Build plan-only system prompt fragment. */
export function planModeSystemPrompt(): string {
  return _PLAN_MODE_SYSTEM_PROMPT;
}

/** Build the plan confirmation footer. */
export function planModeFooter(): string {
  return _PLAN_MODE_FOOTER;
}
