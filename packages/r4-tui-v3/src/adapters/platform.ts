/**
 * Platform adapter — wraps DataSource behind the PlatformAdapter interface.
 *
 * This is the single place where concrete backend selection happens.
 *
 * Architectural Invariant:
 *  - This file is an ADAPTER — it imports from stores (type reuse) but
 *    stores MUST NOT import from here.
 */

import type { PlatformAdapter, PlatformConfig } from "../types/platform";
import type { DataSource } from "./types";
import type { WorkspaceState } from "../stores";

// ── Platform Adapter Implementation ─────────────────────────────────────

export class R4PlatformAdapter implements PlatformAdapter {
  private readonly source: DataSource;
  private configPromise: Promise<PlatformConfig> | null = null;

  constructor(source: DataSource) {
    this.source = source;
  }

  // ── Platform Config ─────────────────────────────────────────────────

  private async buildConfig(): Promise<PlatformConfig> {
    const readonly = this.source.name === "offline";
    return {
      name: this.source.name,
      sessionId: Math.random().toString(36).slice(2, 14),
      startedAt: new Date().toISOString(),
      healthy: true,
      quality: "good",
      readonly,
      capabilities: {
        streamingChat: typeof this.source.chatStream === "function",
        endpointProvisioning: typeof this.source.provisionEndpoint === "function",
        jobManagement:
          typeof this.source.cancelJob === "function" ||
          typeof this.source.retryJob === "function",
        knowledgePipeline: typeof this.source.pollProjectKnowledge === "function",
        skillsInventory: false,
      },
    };
  }

  async getConfig(): Promise<PlatformConfig> {
    if (!this.configPromise) {
      this.configPromise = this.buildConfig();
    }
    return this.configPromise;
  }

  async healthCheck(): Promise<"excellent" | "good" | "degraded" | "unavailable"> {
    try {
      const snapshot = await this.source.pollBackend();
      return snapshot.problems?.length ? "degraded" : "good";
    } catch {
      return "unavailable";
    }
  }

  // ── Workspace / Backend ─────────────────────────────────────────────

  async loadWorkspace() {
    return this.source.loadWorkspace();
  }

  async pollBackend() {
    return this.source.pollBackend();
  }

  // ── Chat ────────────────────────────────────────────────────────────

  async chat(message: string, history?: import("./types").ChatHistoryItem[], model?: string) {
    return this.source.chat(message, history, model);
  }

  async *chatStream(
    message: string,
    history?: import("./types").ChatHistoryItem[],
    model?: string,
    signal?: AbortSignal,
  ) {
    if (!this.source.chatStream) return;
    yield* this.source.chatStream(message, history, model, signal);
  }

  // ── Backend Operations ──────────────────────────────────────────────

  async provisionEndpoint(model: string, tier?: string) {
    if (!this.source.provisionEndpoint) {
      return { ok: false, message: "Provisioning not supported by this platform." };
    }
    return this.source.provisionEndpoint(model, tier);
  }

  async stopEndpoint(id: string) {
    if (!this.source.stopEndpoint) {
      return { ok: false, message: "Endpoint stop not supported by this platform." };
    }
    return this.source.stopEndpoint(id);
  }

  async cancelJob(id: string) {
    if (!this.source.cancelJob) {
      return { ok: false, message: "Job cancellation not supported by this platform." };
    }
    return this.source.cancelJob(id);
  }

  async retryJob(id: string) {
    if (!this.source.retryJob) {
      return { ok: false, message: "Job retry not supported by this platform." };
    }
    return this.source.retryJob(id);
  }

  async getJobLogs(id: string, lines?: number) {
    if (!this.source.getJobLogs) {
      return "(platform does not support log retrieval)";
    }
    return this.source.getJobLogs(id, lines);
  }

  async pollProjectKnowledge(projectId: string, projectName?: string) {
    if (!this.source.pollProjectKnowledge) {
      return {
        knowledge: [], tasks: [], ingestion: null,
        coverage: null, readiness: null, plan: null, loop: null, sessions: null,
      };
    }
    return this.source.pollProjectKnowledge(projectId, projectName);
  }

  // ── ADR-031 server-spine reads ───────────────────────────────────────

  private spineUnavailable(verb: string): Record<string, unknown> {
    return { error: `server-spine read '${verb}' not supported by this platform` };
  }

  async catalogSearch(query: string, opts: { limit?: number; modality?: string; state?: string; tag?: string; prefix?: string } = {}) {
    if (!this.source.catalogSearch) return this.spineUnavailable("catalogSearch");
    return this.source.catalogSearch(query, opts);
  }

  async scientificCatalogs(projectId?: string) {
    if (!this.source.scientificCatalogs) return this.spineUnavailable("scientificCatalogs");
    return this.source.scientificCatalogs(projectId);
  }

  async catalogRead(s3Key: string, opts: { offset?: number; length?: number; source?: string } = {}) {
    if (!this.source.catalogRead) return this.spineUnavailable("catalogRead");
    return this.source.catalogRead(s3Key, opts);
  }

  async catalogStats() {
    if (!this.source.catalogStats) return this.spineUnavailable("catalogStats");
    return this.source.catalogStats();
  }

  async knowledgeStatus(projectId: string) {
    if (!this.source.knowledgeStatus) return this.spineUnavailable("knowledgeStatus");
    return this.source.knowledgeStatus(projectId);
  }

  async knowledgeFindings(projectId: string) {
    if (!this.source.knowledgeFindings) return this.spineUnavailable("knowledgeFindings");
    return this.source.knowledgeFindings(projectId);
  }

  async listGraphs(projectId?: string, opts: { status?: string; query?: string; limit?: number } = {}) {
    if (!this.source.listGraphs) return this.spineUnavailable("listGraphs");
    return this.source.listGraphs(projectId, opts);
  }

  async graphContent(graphId: string, opts: { nodeLimit?: number; edgeLimit?: number } = {}) {
    if (!this.source.graphContent) return this.spineUnavailable("graphContent");
    return this.source.graphContent(graphId, opts);
  }

  async graphProposals(graphId: string, opts: { status?: string } = {}) {
    if (!this.source.graphProposals) return this.spineUnavailable("graphProposals");
    return this.source.graphProposals(graphId, opts);
  }

  async decideGraphProposal(proposalId: string, approved: boolean) {
    if (!this.source.decideGraphProposal) return this.spineUnavailable("decideGraphProposal");
    return this.source.decideGraphProposal(proposalId, approved);
  }

  async knowledgeQuery(projectId: string, query: string, opts: { scope?: string; limit?: number } = {}) {
    if (!this.source.knowledgeQuery) return this.spineUnavailable("knowledgeQuery");
    return this.source.knowledgeQuery(projectId, query, opts);
  }

  async reconcileKnowledge(projectId: string, sample = 50) {
    if (!this.source.reconcileKnowledge) return this.spineUnavailable("reconcileKnowledge");
    return this.source.reconcileKnowledge(projectId, sample);
  }

  async knowledgeLineage(artifactId: string, projectId: string, depth = 3) {
    if (!this.source.knowledgeLineage) return this.spineUnavailable("knowledgeLineage");
    return this.source.knowledgeLineage(artifactId, projectId, depth);
  }
}

// ── Factory ─────────────────────────────────────────────────────────────

export function createPlatformAdapter(source: DataSource): PlatformAdapter {
  return new R4PlatformAdapter(source);
}

// ── PlatformKind inference ─────────────────────────────────────────────

export function inferPlatformKind(sourceName: string): "cli" | "remote" | "offline" | "embedded" {
  switch (sourceName) {
    case "r4-cli": return "cli";
    case "offline": return "offline";
    default: return "embedded";
  }
}
