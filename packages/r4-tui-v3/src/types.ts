/**
 * R4 Console v3 domain types.
 */

export type Space =
  | "dashboard"
  | "research"
  | "tasks"
  | "work"
  | "knowledge"
  | "infrastructure"
  | "settings"
  | "loop"
  | "sessions"
  | "codeview"
  | "catalog"
  | "term";

/** Diagnostic tray tabs — chat lives in the main Dashboard pane, not here. */
export type ActivityTab =
  | "agent-logs"
  | "tool-calls"
  | "events"
  | "terminal"
  | "notifications"
  | "todos";

export type ConnectionState = "connecting" | "connected" | "degraded" | "disconnected";

export interface ConnectionStatus {
  gateway: ConnectionState;
  umrs: ConnectionState;
  hpc: ConnectionState;
}

export interface ResearchProject {
  id: string;
  name: string;
  description: string;
  status: "planning" | "active" | "paused" | "completed" | "abandoned";
  goalIds: string[];
}

export interface ResearchGoal {
  id: string;
  projectId: string;
  statement: string;
  status: "proposed" | "active" | "achieved" | "abandoned";
}

export interface ResearchTask {
  id: string;
  goalId: string;
  title: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  assignedAgentId?: string;
}

export interface Agent {
  id: string;
  name: string;
  status: "idle" | "thinking" | "executing" | "waiting" | "error";
  currentTask?: string;
}

export interface Execution {
  id: string;
  taskId: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
}

export interface Evidence {
  id: string;
  executionId: string;
  name: string;
  kind: "file" | "metric" | "table" | "plot";
}

export interface KnowledgeObject {
  id: string;
  name: string;
  status: "empty" | "indexing" | "ready" | "stale" | "error";
  documentCount: number;
  /** K1.3: who added it (server contributions; `/k2/artifacts`). */
  contributors?: string[];
  /** K1.3: the graphs it sits in (ids). */
  graphs?: string[];
  /** K1.3: artifact kind (document, code, …). */
  kind?: string;
  /** K1.3: when it was last added (ISO). */
  addedAt?: string;
}

export interface Publication {
  id: string;
  title: string;
  status: "draft" | "in_review" | "accepted" | "published" | "withdrawn";
}

export interface Citation {
  /** Artifact ID referenced in the answer. */
  artifactId: string;
  /** Kind of artifact (document, model, etc.). */
  kind: string;
  /** Short title for display. */
  title: string;
  /** Preview of the artifact content. */
  contentPreview: string;
  /** Session evidence label (S3): the handle `citation.open` fetches (K1.4). */
  sid?: string;
  /** The passage handle and its project: `citation.open` fetches by them
   * once the session no longer holds the label (review of K1 part A). */
  sourceId?: string;
  projectId?: string;
}

/** The citation chip's passage preview (K1.4): the reply's citations, the
 * one shown, and its passage once `citation.open` answered. */
export interface CitationPreviewState {
  citations: Citation[];
  index: number;
  /** The passage text; null while it loads. */
  text: string | null;
  /** Why the preview shows less than the passage (offline, fetch failed). */
  note: string;
  /** The chunk range the text covers ([first, last]), when the server said. */
  chunks: number[];
}

/** Management card payload pinned to the mgmt-card.v1 contract. */
export type MgmtCard = {
  v: 1;
  /** Card family id (wf-list | wf-run | wf-status | wf-results | loop | knowledge-status…). */
  type: string;
  payload: unknown;
};

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: string;
  /** Model/endpoint that produced this reply (assistant messages only). */
  model?: string;
  /** True while the assistant reply is still streaming in. */
  streaming?: boolean;
  /** True while this user message waits behind the in-flight turn (T5
   * topic-15: renders the "Queued" chip so the user knows it hasn't
   * reached the agent yet; cleared when the queue pops it). */
  queued?: boolean;
  /** True when the reply came from the offline fallback, not a live backend. */
  fallback?: boolean;
  /** Citations referencing real artifact IDs from the knowledge store. */
  citations?: Citation[];
  /** Optional structured management card rendered next to/beside the text. */
  mgmtCard?: MgmtCard;
  /** Keyed system notices update in place (e.g. cumulative tool-activity summary). */
  noticeKey?: string;
  /** B5: structured error card (what failed / what the app did / verb). */
  errorCard?: ErrorCard;
  /**
   * Tool calls that produced this assistant reply, in execution order.
   * Rendered as an expandable group at the top of the message cell so the
   * transcript reads message → agent work → message (chronological).
   */
  toolCalls?: import("./lib/tool-activity").ActivityLine[];
  /** Companion session turn id (ADR-057 `tN`) this user message opened:
   * send_message's `turn_id`, or a snapshot row's `metadata.turn_id`.
   * Rewind cuts the companion history by it (TUI audit r2 #17). */
  turnId?: string;
}

/** B5: one card type for bridge disconnects, tool failures, dispatch/RPC errors. */
export interface ErrorCard {
  /** What failed (one line). */
  what: string;
  /** What the app did about it. */
  action: "dropped" | "retrying" | "needs_you";
  /** Technical detail (kept out of the headline). */
  detail?: string;
  /** True when the card can be retried (Enter on an empty draft, or click). */
  retryable?: boolean;
  /** The retry verb's label with its key, when it is not a plain retry
   * ("click: reload this tab"). */
  verb?: string;
  /** One more recovery line under the verb (a command the user can type). */
  alt?: string;
}

/**
 * Session sync badge (plan 2026-09-09 §8a decision 5 — fixed vocabulary,
 * 1:1 with the server sync_status modes; no synonyms anywhere).
 */
export type SessionSyncBadge = "" | "local-only" | "remote-only" | "mirrored" | "degraded";

/** Wave D: server session record for the /sessions pane. */
export interface SessionRecord {
  id: string;
  name: string;
  model: string;
  created_at: string;
  updated_at: string;
  pinned?: boolean;
  archived_at?: string;
  turn_count?: number;
  cost_usd?: number;
  /** Venue grammar (plan §4.1): "local" | "frontend" | "cluster:<name>" | ""
   *  ("" = pre-venue row, never fabricated — venue unknown client-side). */
  venue?: string;
  /** Canonical state enum: "" | "ongoing" | "stopped" | "completed" | "error". */
  state?: string;
  /** Merge badge from the unified listing (server-computed). */
  sync?: SessionSyncBadge;
}

/** Venue picker choices (round 1: cluster:<name> arrives with WP-05 round 2). */
export type VenueChoice = "local" | "frontend" | `cluster:${string}`;

/** /sessions filter chips (plan §4.5/§8a-4). */
export type SessionStateFilter = "all" | "ongoing" | "stopped" | "completed" | "error" | "archived";
export type SessionVenueFilter = "all" | "local" | "frontend";

export interface SessionFilters {
  state: SessionStateFilter;
  venue: SessionVenueFilter;
  includeArchived: boolean;
}

/**
 * ADR-037: per-step tool-selection record (tool_selection control envelope).
 * Fields mirror contracts/control/v1/tool_selection.schema.json — keep in sync.
 */
export interface ToolSelectionRecord {
  version: number;
  session_id: string;
  turn_id: string;
  step_index: number;
  available_tools: number;
  discovered: Record<string, unknown>;
  filtered_out: Record<string, { count?: number; sample?: string[] }>;
  candidate_tools: number;
  selected_tools: string[];
  selected_skills: string[];
  strategy: "passthrough" | "policy_filter" | "ranked";
  ranking: unknown;
  selection_latency_ms: number;
  schema_tokens_estimate: { before?: number; after?: number; skill_bodies?: number };
  /** ADR-037 amendment: catalog-integrity warnings (double registrations). */
  warnings?: string[];
  /** ADR-037 amendment (Finding A, shadow mode): measured only — no count when
   *  no capability-manifest data path exists (never a fabricated size). */
  would_filter?: Record<string, { count?: number; sample?: string[] }>;
}

/** C3: per-turn token/cost/context read-model (session/cost envelope). */
export interface CostState {
  session_id: string;
  model: string;
  turn_tokens: number;
  total_tokens: number;
  cost_usd_turn: number;
  cost_usd_total: number;
  price_known: boolean;
  context_used_tokens: number;
  context_limit_tokens: number;
}

export interface Notification {
  id: string;
  title: string;
  message: string;
  severity: "info" | "warning" | "error" | "success";
  read: boolean;
  timestamp: string;
}

export interface ConsoleEvent {
  id: string;
  timestamp: string;
  type: "agent" | "tool" | "artifact" | "job" | "knowledge" | "system";
  actor: string;
  action: string;
  detail?: string;
}

export interface NavNode {
  id: string;
  label: string;
  icon?: string;
  expanded?: boolean;
  children?: NavNode[];
  route?: string;
}

export interface Command {
  id: string;
  namespace: string;
  title: string;
  description?: string;
  shortcut?: string;
  slashName?: string;
  slashDescription?: string;
  slashTakesArgs?: boolean;
  run: () => void;
}

// Backend entities mapped from r4 CLI / Gateway
export interface UiEndpoint {
  id: string;
  model: string;
  servedModelName: string;
  /** Registered source this endpoint came from (e.g. bdrt, rccs-ds4-m10). */
  sourceName?: string;
  status: "provisioning" | "ready" | "degraded" | "stopped" | "failed";
  url: string;
  tier: "system" | "project" | "user";
  backend: string;
  host: string;
  port: number;
  ownerType?: string;
  createdAt: string;
  readyAt?: string;
  expiresAt?: string;
  /** T5-topic-5 (capability-detected absorption): `readyAt − createdAt`
   * derived at the mapper; NEVER populated when either endpoint-side
   * timestamp is absent (no fabricated badges). */
  coldStartMs?: number;
  /** A configured public source's row (`instance_id: "public"`): no model,
   * no live state; label it with lib/endpoint-label, never "unknown". */
  isPublic?: boolean;
}

export interface UiModel {
  id: string;
  name: string;
  scope: "system" | "project" | "user";
  type?: string;
  status?: string;
  /** `r4 model list --json`: the source that serves this model. */
  source?: string;
  /** `r4 model list --json`: that source's kind ("platform" | "public"). */
  kind?: string;
}

export interface UiJob {
  id: string;
  type: string;
  status: "planned" | "queued" | "running" | "completed" | "failed" | "cancelled";
  projectId?: string;
  createdAt?: string;
  updatedAt?: string;
  /** P5 (capability-detected absorption): stamped by the backend when a
   * reasoning marker exists (e.g. ADR-048 budget_exhausted family). Rows
   * without the marker stay hidden, never a fabricated badge. */
  stateReason?: string;
  /** P5: attempt-phase span joint (queue/claim/execute/finalize per ADR-047
   * wave) when the backend attaches one; raw span label byte-preserved. */
  attemptPhase?: string;
  /** P5: ADR-048 wrapper marker — exact terminal reason verbatim from the
   * venue info drop when present (`budget_exhausted` family); never invented. */
  terminalReason?: string;
  /** P5: durable attempt counter commit from the backend (`attempts` int). */
  attempts?: number;
  /** P5: max attempts budget (`desired_max_attempts`) — chip rendered only
   * when present (ADR-048 attempt-budget envelope visual). */
  desiredMaxAttempts?: number;
  /** P5: backend restart-policy row (`retry|never|...`); a rubric that
   * never silently requeues — rendered as a chip only when present. */
  restartPolicy?: string;
}

export interface UiSkill {
  name: string;
  source: string;
  hasMcp: boolean;
  description: string;
  /** Bridge-authoritative: currently activated for the session. */
  active?: boolean;
  /** Bridge-authoritative: tool names this skill allows. */
  tools?: string[];
}

/** One session file edit with cumulative before→after and a bounded diff. */
export interface SessionFileChange {
  path: string;
  before: string;
  after: string;
  inserted?: number;
  deleted?: number;
  edits?: number;
  diff_text?: string;
}

/** One structured session-plan step (bridge task-graph mirror of todo_write). */
export interface SessionPlanStep {
  id: string;
  title: string;
  status: string; // pending | active | done | failed
  progress: number;
  depends_on: string[];
}

/** Governance overview sections (tolerant fetch: any may carry error). */
export interface GovernanceOverview {
  ownership?: Record<string, unknown> | { error?: string };
  quotas?: Record<string, unknown> | { error?: string };
  audit?: Record<string, unknown> | { error?: string };
}

// ── Agent Runtime Types ─────────────────────────────────────────────────

export type AgentState =
  | "idle"
  | "planning"
  | "waiting"
  | "running"
  | "blocked"
  | "review"
  | "completed"
  | "failed"
  | "cancelled";

export type AgentRole =
  | "literature"
  | "experiment"
  | "analysis"
  | "reviewer"
  | "planner"
  | "critique"
  | "companion"
  | "rc1";

export interface AgentBudget {
  maxTokens?: number;
  maxIterations?: number;
  maxWallClock?: string;
  maxGpus?: number;
}

export interface AgentPermissions {
  egress: "none" | "internet" | "controlled";
  filesystem: "read_only" | "read_write_scratch" | "read_write";
  compute: "none" | "slurm" | "local";
}

export interface AgentRuntime {
  id: string;
  role: AgentRole;
  goal: string;
  state: AgentState;
  currentTask?: string;
  currentObjective?: string;
  currentEvidence?: string[];
  confidence?: number;
  reasoning?: string[];
  budget: AgentBudget;
  permissions: AgentPermissions;
  createdAt: string;
  updatedAt: string;
  progress?: number;
  errorMessage?: string;
}

// ── Research Queue Types ────────────────────────────────────────────────

export type ResearchIntent =
  | "read_paper"
  | "extract_knowledge"
  | "verify_knowledge"
  | "generate_hypothesis"
  | "design_experiment"
  | "submit_hpc_job"
  | "analyze_results"
  | "review_findings"
  | "prepare_publication";

export interface ResearchQueueItem {
  id: string;
  intent: ResearchIntent;
  hypothesisId?: string;
  experimentId?: string;
  agentId?: string;
  priority: number;
  dependencies: string[];
  status: "pending" | "in_progress" | "completed" | "failed" | "cancelled";
  createdAt: string;
  updatedAt: string;
}

// ── Scientific Event Types ──────────────────────────────────────────────

export type ScientificEventType =
  | "knowledge_created"
  | "knowledge_updated"
  | "knowledge_corrected"
  | "knowledge_approved"
  | "hypothesis_generated"
  | "hypothesis_tested"
  | "hypothesis_supported"
  | "hypothesis_rejected"
  | "experiment_scheduled"
  | "experiment_started"
  | "experiment_progress"
  | "experiment_completed"
  | "experiment_failed"
  | "experiment_analyzed"
  | "finding_created"
  | "finding_resolved"
  | "publication_drafted"
  | "publication_reviewed"
  | "publication_created"
  | "agent_started"
  | "agent_progress"
  | "agent_reasoning"
  | "agent_output"
  | "agent_paused"
  | "agent_completed"
  | "agent_failed"
  | "approval_requested"
  | "approval_granted"
  | "approval_denied"
  | "workflow_node_entered"
  | "workflow_node_exited"
  | "workflow_edge_traversed";

export interface ScientificEvent {
  id: string;
  eventType: ScientificEventType;
  timestamp: string;
  projectId?: string;
  agentId?: string;
  payload: Record<string, unknown>;
  provenance: string[];
  correlationId?: string;
}

// ── Timeline Types ──────────────────────────────────────────────────────

export type TimelineEntryType =
  | "artifact_upload"
  | "knowledge_extraction"
  | "graph_update"
  | "hypothesis_created"
  | "experiment_started"
  | "experiment_completed"
  | "finding_registered"
  | "review_completed"
  | "knowledge_correction"
  | "publication_created";

export interface TimelineEntry {
  id: string;
  type: TimelineEntryType;
  timestamp: string;
  title: string;
  description?: string;
  agentId?: string;
  objectId?: string;
  metadata?: Record<string, unknown>;
}

// ── Approval Gate Types ─────────────────────────────────────────────────

export type ApprovalType =
  | "knowledge_extraction"
  | "hypothesis_generation"
  | "experiment_design"
  | "experiment_execution"
  | "publication_draft";

export type ApprovalStatus = "pending" | "approved" | "rejected" | "modified";

export interface ApprovalGate {
  id: string;
  type: ApprovalType;
  status: ApprovalStatus;
  title: string;
  description?: string;
  agentId?: string;
  objectId?: string;
  requestedAt: string;
  resolvedAt?: string;
  comment?: string;
}

// ── Workflow Graph Types ────────────────────────────────────────────────

export type WorkflowNodeType =
  | "agent"
  | "knowledge"
  | "experiment"
  | "decision"
  | "review"
  | "publication";

export type WorkflowNodeStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "blocked"
  | "waiting";

export interface WorkflowNode {
  id: string;
  type: WorkflowNodeType;
  label: string;
  status: WorkflowNodeStatus;
  agentId?: string;
  objectId?: string;
  metadata?: Record<string, unknown>;
}

export interface WorkflowEdge {
  from: string;
  to: string;
  label?: string;
}

export interface WorkflowGraph {
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
}

// ── Mission Control Types ────────────────────────────────────────────────

export interface MissionControlState {
  researchGoal: string;
  successCriteriaProgress: number;
  activeAgents: AgentRuntime[];
  runningExperiments: ResearchQueueItem[];
  pendingApprovals: ApprovalGate[];
  blockers: { id: string; description: string; severity: "info" | "warning" | "error" }[];
  computeBudget: { remaining: number; burnRate: number; estimatedRunway: number };
  hypotheses: { id: string; statement: string; confidence: number }[];
}

// ── Explainability Types ────────────────────────────────────────────────

export interface DecisionExplanation {
  decisionId: string;
  decisionType: string;
  target: string;
  reasoningChain: string[];
  graphPaths: string[][];
  supportingEvidence: { id: string; description: string; confidence: number }[];
  confidenceCalculation: { prior: number; likelihood: number; posterior: number };
  alternatives: { id: string; description: string; score: number }[];
  rejected: { id: string; description: string; reason: string }[];
  timestamp: string;
  agentId: string;
}

// ── Verification Types (Phase 10) ─────────────────────────────────────

export interface VerificationAnswer {
  question: string;
  answer: string;
  reasoningPath: string[];
  evidenceIds: string[];
  graphTraversal: string[];
  confidence: number;
  supportingPapers: string[];
  supportingExperiments: string[];
  relatedHypotheses: string[];
}

// ── Knowledge Graph Types (Phase 4) ────────────────────────────────────

export type KnowledgeNodeType =
  | "hypothesis"
  | "experiment"
  | "evidence"
  | "finding"
  | "paper"
  | "dataset"
  | "code"
  | "notebook"
  | "figure"
  | "goal"
  | "task"
  | "knowledge";

export type KnowledgeEdgeType =
  | "cites"
  | "implements"
  | "uses"
  | "references"
  | "supports"
  | "contradicts"
  | "derives_from"
  | "produces"
  | "answers"
  | "verifies"
  | "refutes"
  | "assumes"
  | "related_to"
  | "decomposes";

export interface KnowledgeNode {
  id: string;
  label: string;
  type: KnowledgeNodeType;
  status: string;
  confidence?: number;
  metadata?: Record<string, unknown>;
}

export interface KnowledgeEdge {
  from: string;
  to: string;
  type?: KnowledgeEdgeType;
  label?: string;
  confidence?: number;
}

// ── Research Strategy Engine Types (Phase 11) ──────────────────────────

export type ActionType =
  | "read_paper"
  | "extract_knowledge"
  | "verify_knowledge"
  | "generate_hypothesis"
  | "design_experiment"
  | "submit_hpc_job"
  | "analyze_results"
  | "review_findings"
  | "prepare_publication";

export interface ActionCandidate {
  id: string;
  actionType: ActionType;
  description: string;
  score: number; // 0..1, higher = better
  rationale: string;
  estimatedCost: number; // GPU-hours
  estimatedDuration: number; // hours
  dependencies: string[];
  targetHypothesisId?: string;
  targetExperimentId?: string;
}

export interface StrategyAssessment {
  assessmentId: string;
  timestamp: string;
  overallScore: number; // 0..1
  strengths: string[];
  weaknesses: string[];
  recommendations: string[];
  coverageGaps: string[];
  budgetUtilization: number; // 0..1
  riskLevel: "low" | "medium" | "high";
}

export interface ExperimentProposal {
  id: string;
  hypothesisId: string;
  title: string;
  description: string;
  methodology: string;
  expectedOutcome: string;
  estimatedGpus: number;
  estimatedRuntime: number; // hours
  priority: number;
  riskLevel: "low" | "medium" | "high";
  dependencies: string[];
}

export interface BudgetAllocation {
  totalBudget: number; // GPU-hours
  allocated: number;
  remaining: number;
  allocations: {
    agentId: string;
    agentRole: AgentRole;
    allocated: number;
    utilized: number;
    remaining: number;
  }[];
  burnRate: number; // GPU-hours/day
  estimatedRunway: number; // days
}

// ── Scientific Blackboard Types (Phase 12) ─────────────────────────────

export type BlackboardEntryType =
  | "observation"
  | "hypothesis"
  | "experiment"
  | "question"
  | "critique"
  | "finding"
  | "idea"
  | "review"
  | "publication";

export type BlackboardEntryStatus = "draft" | "active" | "resolved" | "superseded" | "rejected";

export interface BlackboardEntry {
  id: string;
  type: BlackboardEntryType;
  status: BlackboardEntryStatus;
  title: string;
  content: string;
  authorAgentId?: string;
  authorRole?: AgentRole;
  createdAt: string;
  updatedAt: string;
  provenance: string[]; // chain of entry IDs that led to this one
  relatedHypothesisId?: string;
  relatedExperimentId?: string;
  confidence?: number;
  critiques: Critique[];
  tags: string[];
}

export interface Critique {
  id: string;
  entryId: string;
  content: string;
  criticAgentId?: string;
  criticRole?: AgentRole;
  severity: "info" | "warning" | "error";
  createdAt: string;
  resolved: boolean;
  resolvedAt?: string;
}

// ── Knowledge Module Types (Phase 13 UI Integration) ───────────────────

/** Phase 3: Ingestion result */
export interface IngestionResult {
  projectId: string;
  filesProcessed: number;
  extractedCount: number;
  graphNodes: number;
  status: "pending" | "processing" | "completed" | "failed";
  errors: string[];
}

/** Phase 4: Extraction result */
export interface ExtractionResult {
  sourceId: string;
  extractedEntities: { id: string; type: string; value: string }[];
  extractedRelationships: { from: string; to: string; type: string }[];
  status: "pending" | "processing" | "completed" | "failed";
}

/** Phase 6: Verification result */
export interface VerificationResult {
  question: string;
  answer: string;
  confidence: number;
  reasoningPath: string[];
  evidenceIds: string[];
  graphTraversal: string[];
  supportingPapers: string[];
  supportingExperiments: string[];
  relatedHypotheses: string[];
}

/** Phase 7: Coverage report */
export interface CoverageReport {
  coveragePercentage: number;
  totalAreas: number;
  coveredAreas: number;
  gaps: { area: string; severity: "low" | "medium" | "high"; description: string }[];
}

/** Phase 9: Memory snapshot */
export interface MemorySnapshot {
  entries: { id: string; type: string; content: string; timestamp: string }[];
  timeline: { timestamp: string; event: string; details: string }[];
  totalEntries: number;
}

/** Phase 10: Readiness assessment */
export interface ReadinessAssessment {
  ready: boolean;
  /** Alias of Companion can_run when present. */
  canRun?: boolean;
  userApproved?: boolean;
  /** An unexpired approval no longer holds (UMRS `approval_stale`). */
  approvalStale?: boolean;
  readinessLevel: "none" | "low" | "medium" | "high";
  readinessChecks: { check: string; passed: boolean; details: string }[];
  blockers: string[];
}

/** Phase 11: Research plan */
export interface ResearchPlan {
  planId: string;
  projectId: string;
  researchGoal: string;
  objectives: { id: string; type: string; description: string; status: string }[];
  hypotheses: { id: string; statement: string; confidence: number; status: string }[];
  experiments: { id: string; title: string; status: string; estimatedGpus: number }[];
  status: "draft" | "proposed" | "approved" | "executing" | "completed" | "failed" | "cancelled";
  createdAt: string;
}

/** Phase 12: Research loop */
export interface ResearchLoop {
  loopId: string;
  projectId: string;
  researchGoal: string;
  status: "running" | "paused" | "stopped" | "completed" | "failed";
  phase: string;
  currentIteration: number;
  maxIterations: number;
  totalHypothesesTested: number;
  totalExperimentsRun: number;
  totalFindings: number;
  /** True when run used simulate=true (fabricated placeholders). */
  simulated?: boolean;
  iterations: {
    iterationId: string;
    iterationNumber: number;
    phase: string;
    hypothesis: string;
    experimentId: string;
    status: string;
  }[];
}

/** ADR-025: Durable research session */
export interface ResearchSession {
  sessionId: string;
  projectId: string;
  title: string;
  researchGoal: string;
  status: "active" | "paused" | "completed" | "archived";
  authoritativeRuntime: "umrs" | "local" | "hybrid";
  createdAt: string;
  updatedAt: string;
  archivedAt?: string | null;
  loops?: { loopId: string; venue: string; loopRole: string; attachedAt: string }[];
}

/** F4 (cross-subsystem asset index, review): owner-scoped rows via
 * `/umrs/assets` snapshot + `/umrs/assets/patch` rails. Never fabricated —
 * rows represent data actually shipped by the server. */
export interface UiAsset {
  kind: string;
  id: string;
  /** Shape-key (`<kind>/<id>`) — the sha-map key on the patch rail. */
  shapeKey: string;
  projectId?: string;
  title?: string;
  status?: string;
  createdAt?: string;
  updatedAt?: string;
  /** The `sha1-shape-hash` the server sent for this row's shape (used to
   * short-circuit no-op patch rounds). */
  shapeHash?: string;
}
