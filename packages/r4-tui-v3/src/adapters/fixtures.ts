/**
 * Offline demo fixtures for v3.
 *
 * Seminar mock: video + catalog + knowledge. DEMO MODE banner is required.
 */

import type { ResearchProject, ResearchGoal, ResearchTask, KnowledgeObject, Agent, Notification, ConsoleEvent, UiEndpoint, UiModel, UiJob } from "../types";

export const demoProject: ResearchProject = {
  id: "proj-video-behaviour",
  name: "Video behaviour study",
  description: "Which conditions produce the most stable movement pattern?",
  status: "active",
  goalIds: ["goal-1", "goal-2"],
};

export const demoGoals: ResearchGoal[] = [
  { id: "goal-1", projectId: demoProject.id, statement: "Catalog long-term video and analysis plans", status: "active" },
  { id: "goal-2", projectId: demoProject.id, statement: "Link detections to a testable finding", status: "proposed" },
];

export const demoTasks: ResearchTask[] = [
  { id: "task-1", goalId: "goal-1", title: "Ingest inventory + analysis plan", status: "completed" },
  { id: "task-2", goalId: "goal-1", title: "Run YOLO on staged videos", status: "running", assignedAgentId: "agent-1" },
  { id: "task-3", goalId: "goal-2", title: "Aggregate tracks into metrics", status: "pending", assignedAgentId: "agent-2" },
];

export const demoKnowledge: KnowledgeObject[] = [
  { id: "knowledge-1", name: "Video catalog (inventory + plan)", status: "ready", documentCount: 18 },
  { id: "knowledge-2", name: "YOLO detections + metrics", status: "indexing", documentCount: 64 },
];

export const demoAgents: Agent[] = [
  { id: "agent-1", name: "video-agent", status: "executing", currentTask: "running YOLO step" },
  { id: "agent-2", name: "catalog-agent", status: "thinking", currentTask: "indexing detections" },
];

export const demoNotifications: Notification[] = [
  { id: "n-1", title: "Endpoint ready", message: "On-prem vision model is serving", severity: "success", read: false, timestamp: new Date().toISOString() },
];

export const demoEvents: ConsoleEvent[] = [
  { id: "evt-1", timestamp: new Date().toISOString(), type: "agent", actor: "video-agent", action: "started", detail: "video-ai-yolo workflow" },
];

export const demoEndpoints: UiEndpoint[] = [
  { id: "37414", model: "GLM-5.2-NVFP4", servedModelName: "GLM-5.2-NVFP4", status: "ready", url: "http://127.0.0.1:13855", tier: "system", backend: "slurm", host: "127.0.0.1", port: 13855, ownerType: "system", createdAt: new Date().toISOString() },
  { id: "38399", model: "Kimi-K2.6", servedModelName: "Kimi-K2.6", status: "ready", url: "http://127.0.0.1:13863", tier: "user", backend: "slurm", host: "127.0.0.1", port: 13863, createdAt: new Date().toISOString() },
];

export const demoModels: UiModel[] = [
  { id: "glm-5.2", name: "GLM-5.2-NVFP4", scope: "system" },
  { id: "kimi-k2.6", name: "Kimi-K2.6", scope: "user" },
];

export const demoJobs: UiJob[] = [
  { id: "job-1", type: "endpoint_provision", status: "running" },
  { id: "job-2", type: "index_build", status: "completed" },
];

/**
 * DEMO (labeled): ADR-037 tool-selection record fixture. Mirrors the
 * contracts/control/v1/tool_selection.schema.json shape; used by offline
 * demo to render the phase label + roll-up + cost-chip suffix honestly
 * (DEMO MODE banner is the honesty signal — values are fabricated).
 */
export const demoToolSelection = {
  type: "tool_selection",
  payload: {
    version: 1,
    session_id: "demo-session",
    turn_id: "t1",
    step_index: 0,
    available_tools: 142,
    discovered: { session_registered: 142, skills_active: ["video"] },
    filtered_out: {
      disabled: { count: 4, sample: ["shell_exec (user_denied)", "file_write (user_denied)"] },
    },
    candidate_tools: 138,
    selected_tools: ["file_read", "workspace_search", "lab_catalog_search", "workflow_list", "knowledge_query"],
    selected_skills: ["video"],
    strategy: "passthrough" as const,
    ranking: null,
    selection_latency_ms: 1,
    schema_tokens_estimate: { before: 18400, after: 17800, skill_bodies: 3200 },
    // DEMO (fabricated): the live registry carries the same twin pattern.
    warnings: [
      "default-rag-search and hatch-rag-search register the same 3 tools (double registration)",
    ],
    would_filter: {},
  },
};
