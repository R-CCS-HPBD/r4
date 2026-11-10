/** D-70 S4 navigation: ordinary sessions resume; children only inspect. */

import { actions, activeTab } from "../stores";
import type { AgentRuntime, SessionRecord } from "../types";

export function isSubagentSession(record: SessionRecord): boolean {
  return record.session_kind === "subagent";
}

/** Open a child read-only, returning false when the row has no durable child id. */
export function openAgentInspection(agent: AgentRuntime): boolean {
  if (!agent.sessionId) return false;
  actions.openSubagentInspector({
    sessionId: agent.sessionId,
    agentId: agent.id,
    parentSessionId: agent.parentSessionId ?? activeTab().sessionId,
    parentAgentId: agent.parentAgentId ?? "",
    venue: activeTab().sessionVenue,
  });
  return true;
}

function childTarget(record: SessionRecord): Omit<import("../types").SubagentInspectorTarget, "ownerTabId"> {
  return {
    sessionId: record.id,
    agentId: record.agent_id || "",
    parentSessionId: record.parent_session_id || activeTab().sessionId,
    parentAgentId: record.parent_agent_id || "",
    venue: record.venue || "",
  };
}

/** Session-picker Enter/click routes a child away from writable load_session. */
export async function openSessionRecord(record: SessionRecord): Promise<boolean> {
  if (!isSubagentSession(record)) return actions.tabOpenSession(record.id, record.name);
  actions.openSubagentInspector(childTarget(record));
  return true;
}
