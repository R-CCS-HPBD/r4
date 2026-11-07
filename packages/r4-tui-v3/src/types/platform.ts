/**
 * Platform types — backends, adapters, and session configuration.
 *
 * This module defines the platform abstraction layer that decouples the
 * stores from any concrete backend implementation.
 *
 * Architectural Invariant:
 *  - Stores MUST import ONLY from this module for platform concerns.
 *  - Adapters MAY import from stores for type reuse, never the reverse.
 *
 * Note: Snapshot types (WorkspaceSnapshot, BackendSnapshot, etc.) are defined
 * in adapters/types.ts to avoid duplication. This file owns platform-specific
 * types only.
 */

// ── Platform Kinds ──────────────────────────────────────────────────────

/**
 * The kind of platform a TUI session is connected to.
 */
export type PlatformKind = "cli" | "remote" | "offline" | "embedded";

/**
 * Connection quality indicator returned by the platform health check.
 */
export type ConnectionQuality = "excellent" | "good" | "degraded" | "unavailable";

// ── Platform Config ─────────────────────────────────────────────────────

export interface PlatformConfig {
  /** Human-readable platform name (e.g. "r4 CLI v0.8.2"). */
  name: string;

  /** Unique session identifier, generated at boot. */
  sessionId: string;

  /** When the session was created (ISO 8601). */
  startedAt: string;

  /** Version of the platform backend (if available). */
  version?: string;

  /** True when the platform is fully operational. */
  healthy: boolean;

  /** Connection quality (set by health check). */
  quality: ConnectionQuality;

  /** True when this platform instance is read-only / demo mode. */
  readonly: boolean;

  /** Capabilities advertised by the backend. */
  capabilities: PlatformCapabilities;
}

export interface PlatformCapabilities {
  /** Supports streaming chat. */
  streamingChat: boolean;
  /** Can provision / manage endpoints. */
  endpointProvisioning: boolean;
  /** Can manage HPC jobs. */
  jobManagement: boolean;
  /** Knowledge pipeline (ingest/extract/verify). */
  knowledgePipeline: boolean;
  /** Returns skills inventory. */
  skillsInventory: boolean;
}

// ── Platform Adapter Interface ──────────────────────────────────────────

/**
 * A platform adapter wraps a concrete backend behind a uniform interface.
 * Stores depend on this interface.  Adapters implement it.
 */
export interface PlatformAdapter {
  /**
   * Returns the current platform configuration.
   */
  getConfig(): Promise<PlatformConfig>;

  /**
   * Health check — returns quality without side effects.
   */
  healthCheck(): Promise<ConnectionQuality>;

  /**
   * Load the full workspace snapshot.
   * Re-exports type from adapters/types for convenience.
   */
  loadWorkspace(): Promise<import("../adapters/types").WorkspaceSnapshot>;

  /**
   * Poll backend state (endpoints, models, jobs).
   */
  pollBackend(): Promise<import("../adapters/types").BackendSnapshot>;

  /**
   * Chat — non-streaming.
   */
  chat(message: string, history?: import("../adapters/types").ChatHistoryItem[], model?: string): Promise<string>;

  /**
   * Chat — streaming.
   */
  chatStream?(
    message: string,
    history?: import("../adapters/types").ChatHistoryItem[],
    model?: string,
    signal?: AbortSignal,
  ): AsyncGenerator<import("../adapters/types").ChatStreamChunk>;

  /**
   * Provision an LLM endpoint.
   */
  provisionEndpoint?(model: string, tier?: string): Promise<import("../adapters/types").ActionResult>;

  /**
   * Stop an LLM endpoint.
   */
  stopEndpoint?(id: string): Promise<import("../adapters/types").ActionResult>;

  /**
   * Cancel a HPC job.
   */
  cancelJob?(id: string): Promise<import("../adapters/types").ActionResult>;

  /**
   * Retry a failed HPC job.
   */
  retryJob?(id: string): Promise<import("../adapters/types").ActionResult>;

  /**
   * Fetch job logs.
   */
  getJobLogs?(id: string, lines?: number): Promise<string>;

  /**
   * Poll project knowledge.
   */
  pollProjectKnowledge?(
    projectId: string,
    projectName?: string,
  ): Promise<import("../adapters/types").ProjectKnowledgePollResult>;

  // ── ADR-031 server-spine reads (UMRS truth; honest error when absent) ──

  /** Science catalogue search (+ cross-plane links). */
  catalogSearch?(query: string, opts?: { limit?: number; modality?: string; state?: string; tag?: string; prefix?: string }): Promise<Record<string, unknown>>;

  /** Byte-bounded read of one catalogue asset. */
  catalogRead?(s3Key: string, opts?: { offset?: number; length?: number; source?: string }): Promise<Record<string, unknown>>;

  /** Catalogue stats incl. downstream plane counts. */
  catalogStats?(): Promise<Record<string, unknown>>;
  /** D-39 Scientific Catalogs (named, versioned objects — NOT the data catalog). */
  scientificCatalogs?(projectId?: string): Promise<Record<string, unknown>>;

  /** Project knowledge status. */
  knowledgeStatus?(projectId: string): Promise<Record<string, unknown>>;

  /** Verification findings. */
  knowledgeFindings?(projectId: string): Promise<Record<string, unknown>>;

  /** Multi-graph registry list (0069). */
  listGraphs?(projectId?: string, opts?: { status?: string; query?: string; limit?: number }): Promise<Record<string, unknown>>;

  /** Bounded nodes+edges of one registered graph. */
  graphContent?(graphId: string, opts?: { nodeLimit?: number; edgeLimit?: number }): Promise<Record<string, unknown>>;

  /** Agent graph-extension proposals. */
  graphProposals?(graphId: string, opts?: { status?: string }): Promise<Record<string, unknown>>;

  /** Human decision on a proposal. */
  decideGraphProposal?(proposalId: string, approved: boolean): Promise<Record<string, unknown>>;

  /** Unified knowledge facade query. */
  knowledgeQuery?(projectId: string, query: string, opts?: { scope?: string; limit?: number }): Promise<Record<string, unknown>>;

  /** Plane reconciler projection (item-level consistency states). */
  reconcileKnowledge?(projectId: string, sample?: number): Promise<Record<string, unknown>>;

  /** Provenance chain for one knowledge artifact. */
  knowledgeLineage?(artifactId: string, projectId: string, depth?: number): Promise<Record<string, unknown>>;
}
