/**
 * Ghost-update gate (T5 topic-19 tail, 2026-09-22).
 *
 * Opencode rule: an agent answering "done/completed/updated/implemented"
 * with NO write-family tool activity inside the same turn is the classic
 * "agent says work happened, no proof" class. This module detects it
 * deterministically so the TUI can surface a banner instead of letting the
 * claim stand silently.
 *
 * Capability-detected: only fires when the markers line up; never injected
 * into plain questions or read-only work.
 *
 * 2026-10-17: the evidence set is every state-changing tool (the RISKY_TOOLS
 * set of r4/src/r4/companion/permissions.py plus the files-modified write
 * family), and a claim must be phrased as the agent's own change ("I've
 * updated", "has been applied"). Bare substrings flagged read-only answers
 * such as "your run completed" or "nothing has changed".
 */

import type { ActivityLine } from "../lib/tool-activity";
import { isWriteFamilyTool } from "./modified-files";

/** Tools whose run is evidence that something changed. Mirrors RISKY_TOOLS in
 * r4/src/r4/companion/permissions.py (tests/ghost-gate.test.ts pins parity);
 * read-only verbs like `file_read`/`grep`/`workflow_status` are NOT evidence. */
const STATE_CHANGE_TOOLS = new Set([
  "shell_exec", "python_exec", "nodejs_exec", "file_write", "file_edit",
  "hpc_submit", "workflow_run", "workflow_trigger", "workflow_cancel",
  "workflow_upload", "workflow_approve_gate", "job_cancel",
  // ADR-105 S1 moved job_retry and rag_upload to RISKY_TOOLS; S3 added job_commit.
  "job_retry", "rag_upload", "job_commit",
  "teamup_start", "teamup_run", "team_plan_run", "team_plan_cancel",
  "research_loop_start", "research_loop_stop", "research_loop_decide",
  "research_session_create", "knowledge_ingest", "knowledge_build_from_context",
  "knowledge_graph_decide", "project_ensure", "compile_code", "run_code",
  "test_code", "start_language_servers", "source_add", "source_use",
  "source_remove", "pool_up", "pool_down", "demo_prepare", "endpoint_upgrade",
  "endpoint_provision", "endpoint_stop", "mcp_register_server",
  "mcp_unregister_server", "apply_patch",
  // ADR-108 W1c: leaving a worktree may remove it.
  "worktree_exit",
]);

/** Past-tense verbs an agent uses to claim it changed something. */
const CLAIM_VERBS =
  "created|updated|implemented|fixed|applied|changed|modified|wrote|written|" +
  "added|edited|deleted|removed|refactored|reorganized|replaced|installed|" +
  "deployed|committed|patched|saved|renamed|moved|submitted";
const ADVERBS = "(?:\\s+(?:just|now|also|already|successfully|then|finally))*";

/** "I've updated", "we just added", "has been applied", or a bare "Done." line.
 * Negations ("I haven't changed", "has not been modified") do not match. */
const CLAIM_PATTERNS = [
  new RegExp(`\\b(?:i|we)(?:['’]ve|\\s+have)?${ADVERBS}\\s+(?:${CLAIM_VERBS})\\b`, "i"),
  new RegExp(`\\b(?:has|have)${ADVERBS}\\s+been\\s+(?:${CLAIM_VERBS})\\b`, "i"),
  /^\s*(?:all\s+)?done[.!]*\s*$/im,
];

/** True when the tool ran (not denied/blocked) and can change state. */
function isStateChangeEvidence(line: ActivityLine): boolean {
  if (!line.name || line.blocked || line.status === "warn") return false;
  return STATE_CHANGE_TOOLS.has(line.name.trim().toLowerCase()) || isWriteFamilyTool(line.name);
}

/** Whether any state-changing tool actually ran during the turn. */
export function anyWriteToolRan(lines: ActivityLine[]): boolean {
  return lines.some(isStateChangeEvidence);
}

/** Whether the assistant's final text claims it changed something. */
export function textClaimsWriteCompletion(text: string): boolean {
  if (!text) return false;
  return CLAIM_PATTERNS.some((pattern) => pattern.test(text));
}

export interface GhostGateVerdict {
  verdict: "clean" | "ghost-suspect";
  assistantText: string;
  writeToolCount: number;
  reason?: string;
}

/** Evaluates one completed turn: did the agent say "done" without proving it?
 * `lines` must be the WHOLE turn's tool activity (every round). */
export function ghostGateVerdict(
  lines: ActivityLine[],
  assistantText: string,
): GhostGateVerdict {
  const writeCount = lines.filter(isStateChangeEvidence).length;
  if (writeCount > 0) {
    return { verdict: "clean", assistantText, writeToolCount: writeCount };
  }
  if (!textClaimsWriteCompletion(assistantText)) {
    return { verdict: "clean", assistantText, writeToolCount: 0 };
  }
  return {
    verdict: "ghost-suspect",
    assistantText,
    writeToolCount: 0,
    reason:
      "Agent reported a change but no state-changing tool (file write/edit, code execution, job or workflow submission) ran in this turn. Ask it to verify before trusting the claim.",
  };
}
