/**
 * Data source contract for the console.
 */

import type { WorkspaceState } from "../stores";
import type {
  UiEndpoint,
  UiModel,
  UiJob,
  UiSkill,
  ResearchProject,
  ResearchGoal,
  ResearchTask,
  KnowledgeObject,
  Agent,
  Notification,
  ConsoleEvent,
  IngestionResult,
  CoverageReport,
  ReadinessAssessment,
  ResearchPlan,
  ResearchLoop,
} from "../types";

export interface ProjectKnowledgePollResult {
  knowledge: KnowledgeObject[];
  tasks: ResearchTask[];
  ingestion: IngestionResult | null;
  coverage: CoverageReport | null;
  readiness: ReadinessAssessment | null;
  plan: ResearchPlan | null;
  loop: ResearchLoop | null;
  /** ADR-025: sessions attached to the project (null when backend lacks the route) */
  sessions: import("../types").ResearchSession[] | null;
  /** K1.3 review: why the server's knowledge list could not be read (then
   * `knowledge` is not the project's list; the pane keeps its last rows). */
  knowledgeError?: string | null;
}

export interface WorkspaceSnapshot {
  project: ResearchProject | null;
  goals: ResearchGoal[];
  tasks: ResearchTask[];
  knowledge: KnowledgeObject[];
  agents: Agent[];
  notifications: Notification[];
  events: ConsoleEvent[];
  connection: Partial<WorkspaceState["connection"]>;
}

export interface BackendSnapshot {
  endpoints: UiEndpoint[];
  models: UiModel[];
  jobs: UiJob[];
  skills?: UiSkill[];
  /** Per-probe failures on an otherwise answered poll (a probe that failed,
   * a source a multi-source listing reported in `errors`). The rows of a
   * failed probe are the previous ones, kept — never wiped to []. A
   * non-empty list means the backend is degraded, not connected. */
  problems?: string[];
}

export interface ChatHistoryItem {
  role: "user" | "assistant" | "system";
  content: string;
}

/** A single incremental piece of a streamed chat response. */
export interface ChatStreamChunk {
  /** Text fragment to append to the assistant reply. */
  delta?: string;
  /**
   * Model round boundary: the previous text round ended and a new one is
   * starting (tools ran in between). The store closes the current assistant
   * segment — pinning the tool activity accumulated so far UNDER that text —
   * and opens a fresh segment, so the transcript keeps strict invocation-time
   * order (text₁, tools₁, text₂, tools₂, …) instead of one merged reply with
   * all tool details detached below it.
   */
  roundBreak?: boolean;
  /** Model/endpoint that is producing the reply. */
  model?: string;
  /** True when this chunk originates from the offline fallback path. */
  fallback?: boolean;
  /** Client-measured time-to-first-token (ms) — set on the FIRST delta of a
   * real model turn; absent for round breaks/final markers/offline chunks. */
  ttftMs?: number;
  /** Marks the end of the stream. */
  done?: boolean;
  /** The turn was refused with a typed session error (c3: conflict / corrupt
   * row); the store posts its recovery instead of a failure reply. */
  sessionError?: import("../bridge/session-recovery").SessionErrorInfo;
  transcript?: import("../types").ChatMessage[];
  /** Companion session turn id the sent user message opened (tags its row). */
  turnId?: string;
  /** Citations referencing knowledge artifact IDs (final chunk only). */
  citations?: Array<{
    artifactId: string;
    kind: string;
    title: string;
    contentPreview: string;
    sid?: string;
    sourceId?: string;
    projectId?: string;
  }>;
}

/** Result of a backend mutation (endpoint/job action). */
export interface ActionResult {
  ok: boolean;
  message: string;
}

export interface DataSource {
  readonly name: string;
  loadWorkspace(): Promise<WorkspaceSnapshot>;
  pollBackend(): Promise<BackendSnapshot>;
  chat(message: string, history?: ChatHistoryItem[], model?: string): Promise<string>;
  /**
   * Stream a chat response token-by-token. Implementations should always
   * yield a terminal `{ done: true }` chunk and never throw — on failure they
   * fall back to a coherent offline reply flagged with `fallback: true`.
   */
  chatStream?(
    message: string,
    history?: ChatHistoryItem[],
    model?: string,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamChunk>;
  // ── Backend operations (optional; offline source returns canned results) ──
  provisionEndpoint?(model: string, tier?: string): Promise<ActionResult>;
  stopEndpoint?(id: string): Promise<ActionResult>;
  cancelJob?(id: string): Promise<ActionResult>;
  retryJob?(id: string): Promise<ActionResult>;
  getJobLogs?(id: string, lines?: number): Promise<string>;
  /** Read persisted project knowledge files (manifest/plan); live CLI mode only. */
  pollProjectKnowledge?(projectId: string, projectName?: string): Promise<ProjectKnowledgePollResult>;
  /** Knowledge graph Q&A — live CLI or offline fixtures. */
  verifyKnowledge?(projectId: string, question: string): Promise<Record<string, unknown>>;
  /** Explicit readiness RPC (not included in live poll). */
  readiness?(projectId: string): Promise<Record<string, unknown>>;
  // ── ADR-031 server-spine reads (UMRS wire payloads, mapped by the store) ──
  /** Science catalogue search (+ cross-plane links hydration). */
  catalogSearch?(query: string, opts?: { limit?: number; modality?: string; state?: string; tag?: string; prefix?: string }): Promise<Record<string, unknown>>;
  /** Byte-bounded read of one catalogue asset. */
  catalogRead?(s3Key: string, opts?: { offset?: number; length?: number; source?: string }): Promise<Record<string, unknown>>;
  /** Catalogue stats incl. downstream plane counts (null = not computable). */
  catalogStats?(): Promise<Record<string, unknown>>;
  /** Project knowledge status (artifacts, graph stats, verification). */
  knowledgeStatus?(projectId: string): Promise<Record<string, unknown>>;
  /** Verification findings for a project. */
  knowledgeFindings?(projectId: string): Promise<Record<string, unknown>>;
  /** D-39 Scientific Catalogs (named, versioned objects — NOT the data catalog). */
  scientificCatalogs?(projectId?: string): Promise<Record<string, unknown>>;
  /** Multi-graph registry list (0069). */
  listGraphs?(projectId?: string, opts?: { status?: string; query?: string; limit?: number }): Promise<Record<string, unknown>>;
  /** Bounded nodes+edges of one registered graph. */
  graphContent?(graphId: string, opts?: { nodeLimit?: number; edgeLimit?: number }): Promise<Record<string, unknown>>;
  /** Agent graph-extension proposals awaiting human decision. */
  graphProposals?(graphId: string, opts?: { status?: string }): Promise<Record<string, unknown>>;
  /** Human decision on a proposal. */
  decideGraphProposal?(proposalId: string, approved: boolean): Promise<Record<string, unknown>>;
  /** Unified knowledge facade query (+ wired/unavailable backend truth). */
  knowledgeQuery?(projectId: string, query: string, opts?: { scope?: string; limit?: number }): Promise<Record<string, unknown>>;
  /** Plane reconciler read-side projection (item-level consistency states). */
  reconcileKnowledge?(projectId: string, sample?: number): Promise<Record<string, unknown>>;
  /** Provenance chain for one knowledge artifact. */
  knowledgeLineage?(artifactId: string, projectId: string, depth?: number): Promise<Record<string, unknown>>;
}

// ── Knowledge Adapter Interface (Phase 13) ──────────────────────────────

export interface KnowledgeAdapter {
  readonly name: string;
  ingest(projectId: string, files: string[]): Promise<Record<string, unknown>>;
  extract(projectId: string, sourceId: string): Promise<Record<string, unknown>>;
  link(projectId: string, sourceId: string, targetId: string, relationshipType: string): Promise<Record<string, unknown>>;
  verify(projectId: string, question: string): Promise<Record<string, unknown>>;
  coverage(projectId: string): Promise<Record<string, unknown>>;
  memory(projectId: string): Promise<Record<string, unknown>>;
  readiness(projectId: string): Promise<Record<string, unknown>>;
  plan(projectId: string, researchGoal: string): Promise<Record<string, unknown>>;
  startLoop(projectId: string, researchGoal: string, maxIterations: number): Promise<Record<string, unknown>>;
  stopLoop(projectId: string, loopId: string): Promise<Record<string, unknown>>;
  getLoop(projectId: string, loopId: string): Promise<Record<string, unknown>>;
  listLoops(projectId: string): Promise<Record<string, unknown>>;
  /** ADR-025 research sessions (durable, UMRS-owned) */
  listSessions(projectId: string): Promise<Record<string, unknown>>;
  createSession?(projectId: string, researchGoal: string, title?: string): Promise<Record<string, unknown>>;
  getSession?(sessionId: string): Promise<Record<string, unknown>>;
  correct(projectId: string, nodeId: string, correction: string): Promise<Record<string, unknown>>;
  // ── ADR-031 server-spine surfaces (UMRS-owned truth) ─────────────────
  /** Science catalogue plane: search assets (+ links hydration). */
  catalogSearch?(query: string, opts?: { limit?: number; modality?: string; state?: string; tag?: string; prefix?: string }): Promise<Record<string, unknown>>;
  /** Science catalogue plane: byte-bounded read of one asset. */
  catalogRead?(s3Key: string, opts?: { offset?: number; length?: number; source?: string }): Promise<Record<string, unknown>>;
  /** Catalogue stats incl. downstream plane counts (None = not computable). */
  catalogStats?(): Promise<Record<string, unknown>>;
  /** Project knowledge status (artifacts, graph stats, verification). */
  knowledgeStatus?(projectId: string): Promise<Record<string, unknown>>;
  /** Verification findings for a project. */
  findings?(projectId: string): Promise<Record<string, unknown>>;
  /** Multi-graph registry list (0069). */
  graphs?(projectId?: string, opts?: { status?: string; query?: string; limit?: number }): Promise<Record<string, unknown>>;
  /** Bounded nodes+edges for one registered graph. */
  graphContent?(graphId: string, opts?: { nodeLimit?: number; edgeLimit?: number }): Promise<Record<string, unknown>>;
  /** Pending graph-extension proposals (agent-initiated writes). */
  graphProposals?(graphId: string, opts?: { status?: string }): Promise<Record<string, unknown>>;
  /** Human decision on a proposal (human authorization step). */
  graphDecide?(proposalId: string, approved: boolean): Promise<Record<string, unknown>>;
  /** Unified knowledge facade query (K3): results + wired/unavailable truth. */
  knowledgeQuery?(projectId: string, query: string, opts?: { scope?: string; limit?: number }): Promise<Record<string, unknown>>;
  /** ADR-031 plane reconciler read-side projection (item-level states). */
  reconcile?(projectId: string, sample?: number): Promise<Record<string, unknown>>;
  /** Provenance chain for one knowledge artifact (workstream §7). */
  lineage?(artifactId: string, projectId: string, depth?: number): Promise<Record<string, unknown>>;
  /** D-39 Scientific Catalog registry list (umrs `scientific_catalogs` rows). */
  scientificCatalogs?(projectId?: string): Promise<Record<string, unknown>>;
}
