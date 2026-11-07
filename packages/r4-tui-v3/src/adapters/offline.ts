/**
 * Offline data source — rich demo fixtures.
 */

import type { DataSource, WorkspaceSnapshot, BackendSnapshot, KnowledgeAdapter, ChatStreamChunk, ActionResult, ProjectKnowledgePollResult } from "./types";
import { generateLocalReply } from "../lib/chat-reply";
import {
  demoProject,
  demoGoals,
  demoTasks,
  demoKnowledge,
  demoAgents,
  demoNotifications,
  demoEvents,
  demoEndpoints,
  demoModels,
  demoJobs,
} from "./fixtures";

export class OfflineDataSource implements DataSource {
  readonly name = "offline";

  async loadWorkspace(): Promise<WorkspaceSnapshot> {
    return {
      project: demoProject,
      goals: demoGoals,
      tasks: demoTasks,
      knowledge: demoKnowledge,
      agents: demoAgents,
      notifications: demoNotifications,
      events: demoEvents,
      // Demo fixtures simulate a live platform; DEMO MODE banner carries the honesty signal.
      connection: { gateway: "connected", umrs: "connected", hpc: "connected" },
    };
  }

  async pollBackend(): Promise<BackendSnapshot> {
    // Jitter endpoint status to feel alive.
    const endpoints = demoEndpoints.map((ep) => ({
      ...ep,
      status: ep.status,
    }));
    return { endpoints, models: demoModels, jobs: demoJobs, skills: [] };
  }

  async chat(message: string): Promise<string> {
    // Offline mode returns a canned reply based on keywords.
    return generateLocalReply(message, 0);
  }

  async *chatStream(message: string): AsyncGenerator<ChatStreamChunk> {
    const reply = generateLocalReply(message, 0);
    yield { model: "offline", fallback: true };
    // Emit in word-sized chunks so the UI exercises its streaming path.
    // NOTE: RegExp ctor instead of /(\s+)/ literal — the literal confuses lizard's tokenizer.
    const words = reply.split(new RegExp("(\\s+)"));
    for (const word of words) {
      if (word) yield { delta: word };
    }
    yield { done: true };
  }

  async provisionEndpoint(model: string, tier?: string): Promise<ActionResult> {
    const tierSuffix = tier ? ` [${tier}]` : "";
    return { ok: true, message: `(offline) would provision ${model}${tierSuffix}` };
  }

  async stopEndpoint(id: string): Promise<ActionResult> {
    return { ok: true, message: `(offline) would stop endpoint ${id}` };
  }

  async cancelJob(id: string): Promise<ActionResult> {
    return { ok: true, message: `(offline) would cancel job ${id}` };
  }

  async retryJob(id: string): Promise<ActionResult> {
    return { ok: true, message: `(offline) would retry job ${id}` };
  }

  async getJobLogs(id: string): Promise<string> {
    return `(offline) no logs available for job ${id}`;
  }

  async pollProjectKnowledge(projectId: string): Promise<ProjectKnowledgePollResult> {
    const adapter = new OfflineKnowledgeAdapter();
    const planRaw = await adapter.plan(projectId, demoProject.description);
    const objectives = Array.isArray(planRaw.objectives) ? planRaw.objectives : [];
    const hypotheses = Array.isArray(planRaw.hypotheses) ? planRaw.hypotheses : [];
    const experiments = Array.isArray(planRaw.experiments) ? planRaw.experiments : [];
    const docTotal = demoKnowledge.reduce((sum, k) => sum + k.documentCount, 0);

    return {
      knowledge: demoKnowledge,
      tasks: demoTasks,
      ingestion: {
        projectId,
        filesProcessed: docTotal,
        extractedCount: Math.floor(docTotal * 0.6),
        graphNodes: 42,
        status: "completed",
        errors: [],
      },
      coverage: {
        coveragePercentage: 0.75,
        totalAreas: 8,
        coveredAreas: 6,
        gaps: [
          { area: "pose_coverage", severity: "high", description: "DLC pose not yet linked for two sessions" },
          { area: "cross_session", severity: "medium", description: "Few findings compared across weeks" },
        ],
      },
      readiness: {
        ready: true,
        readinessLevel: "high",
        readinessChecks: [
          { check: "knowledge_base_built", passed: true, details: "Knowledge base has 42 nodes and 128 edges" },
          { check: "hypotheses_generated", passed: true, details: "3 hypotheses generated" },
          { check: "experiments_designed", passed: true, details: "5 experiments designed" },
        ],
        blockers: [],
      },
      plan: {
        planId: String(planRaw.planId ?? "plan-demo"),
        projectId,
        researchGoal: String(planRaw.researchGoal ?? demoProject.description),
        objectives: objectives.map((o) => {
          const obj = o as Record<string, unknown>;
          return {
            id: String(obj.id ?? ""),
            type: String(obj.type ?? ""),
            description: String(obj.description ?? ""),
            status: String(obj.status ?? ""),
          };
        }),
        hypotheses: hypotheses.map((h) => {
          const hyp = h as Record<string, unknown>;
          return {
            id: String(hyp.id ?? ""),
            statement: String(hyp.statement ?? ""),
            confidence: Number(hyp.confidence ?? 0),
            status: String(hyp.status ?? ""),
          };
        }),
        experiments: experiments.map((e) => {
          const exp = e as Record<string, unknown>;
          return {
            id: String(exp.id ?? ""),
            title: String(exp.title ?? ""),
            status: String(exp.status ?? ""),
            estimatedGpus: Number(exp.estimatedGpus ?? 0),
          };
        }),
        status: "approved",
        createdAt: new Date().toISOString(),
      },
      loop: null,
      sessions: [
        {
          sessionId: "sess-1",
          projectId,
          title: "Video behaviour study",
          researchGoal: "Which conditions produce the most stable movement pattern?",
          status: "active" as const,
          authoritativeRuntime: "hybrid" as const,
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString(),
          archivedAt: null,
          loops: [
            {
              loopId: "loop-1",
              venue: "umrs",
              loopRole: "auto",
              attachedAt: new Date().toISOString(),
            },
          ],
        },
      ],
    };
  }

  async verifyKnowledge(projectId: string, question: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().verify(projectId, question);
  }

  async readiness(projectId: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().readiness(projectId);
  }

  // ── ADR-031 server-spine reads (fixtures in real wire shapes) ────────

  async catalogSearch(query: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().catalogSearch(query);
  }

  async scientificCatalogs(projectId?: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().scientificCatalogs!(projectId);
  }

  async catalogRead(s3Key: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().catalogRead(s3Key);
  }

  async catalogStats(): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().catalogStats();
  }

  async knowledgeStatus(projectId: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().knowledgeStatus(projectId);
  }

  async knowledgeFindings(projectId: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().findings(projectId);
  }

  async listGraphs(projectId?: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().graphs(projectId);
  }

  async graphContent(graphId: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().graphContent(graphId);
  }

  async graphProposals(graphId: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().graphProposals(graphId);
  }

  async decideGraphProposal(proposalId: string, approved: boolean): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().graphDecide(proposalId, approved);
  }

  async knowledgeQuery(projectId: string, query: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().knowledgeQuery(projectId, query);
  }

  async reconcileKnowledge(projectId: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().reconcile(projectId);
  }

  async knowledgeLineage(artifactId: string): Promise<Record<string, unknown>> {
    return new OfflineKnowledgeAdapter().lineage(artifactId);
  }
}

export class OfflineKnowledgeAdapter implements KnowledgeAdapter {
  readonly name = "offline-knowledge";

  async ingest(projectId: string, files: string[]): Promise<Record<string, unknown>> {
    return {
      projectId,
      filesProcessed: files.length,
      extractedCount: Math.floor(files.length * 1.5),
      graphNodes: Math.floor(files.length * 2),
      status: "completed",
      errors: [],
    };
  }

  async extract(projectId: string, sourceId: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      sourceId,
      extractedEntities: [],
      extractedRelationships: [],
      status: "completed",
    };
  }

  async link(projectId: string, sourceId: string, targetId: string, relationshipType: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      sourceId,
      targetId,
      relationshipType,
      status: "linked",
    };
  }

  async verify(projectId: string, question: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      question,
      answer: "Based on the knowledge graph, detections from the cataloged videos support a stable movement pattern under condition A.",
      confidence: 0.72,
      reasoningPath: [
        "Catalog lists Tuesday videos plus inventory and analysis plan",
        "YOLO run produced detections linked to those videos",
      ],
      evidenceIds: ["VID-12", "INV-1", "YOLO-17"],
      graphTraversal: ["H7", "tested_by", "YOLO-17", "derived_from", "VID-12"],
      supportingPapers: ["VID-12: session video cataloged with analysis plan"],
      supportingExperiments: ["YOLO-17: detection counts support H7 (mock)"],
      relatedHypotheses: [
        "H7: Condition A yields the most stable movement pattern",
        "H8: Condition B is more stable at night",
      ],
    };
  }

  async coverage(projectId: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      coveragePercentage: 0.75,
      totalAreas: 8,
      coveredAreas: 6,
      gaps: [
        { area: "pose_coverage", severity: "high", description: "DLC pose not yet linked for two sessions" },
        { area: "cross_session", severity: "medium", description: "Few findings compared across weeks" },
      ],
    };
  }

  async memory(projectId: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      entries: [
        { id: "mem-1", type: "experiment", content: "YOLO-17 completed on cataloged Tuesday videos", timestamp: new Date().toISOString() },
        { id: "mem-2", type: "finding", content: "F8: night sessions look less stable than condition A", timestamp: new Date().toISOString() },
      ],
      timeline: [
        { timestamp: new Date().toISOString(), event: "experiment_completed", details: "E17 completed" },
      ],
      totalEntries: 2,
    };
  }

  async readiness(projectId: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      ready: true,
      readinessLevel: "high",
      readinessChecks: [
        { check: "knowledge_base_built", passed: true, details: "Knowledge base has 42 nodes and 128 edges" },
        { check: "hypotheses_generated", passed: true, details: "3 hypotheses generated" },
        { check: "experiments_designed", passed: true, details: "5 experiments designed" },
      ],
      blockers: [],
    };
  }

  async plan(projectId: string, researchGoal: string): Promise<Record<string, unknown>> {
    return {
      planId: "plan-1",
      projectId,
      researchGoal,
      objectives: [
        { id: "obj-1", type: "understanding", description: "Catalog videos with inventory and analysis plan", status: "active" },
        { id: "obj-2", type: "optimization", description: "Test which condition is most stable", status: "pending" },
      ],
      hypotheses: [
        { id: "H7", statement: "Condition A yields the most stable movement pattern", confidence: 0.72, status: "active" },
        { id: "H8", statement: "Condition B is more stable at night", confidence: 0.45, status: "proposed" },
      ],
      experiments: [
        { id: "E17", title: "Run YOLO on staged catalog videos", status: "running", estimatedGpus: 1 },
        { id: "E19", title: "Compare night sessions (H8)", status: "pending", estimatedGpus: 1 },
      ],
      status: "approved",
      createdAt: new Date().toISOString(),
    };
  }

  async startLoop(projectId: string, researchGoal: string, maxIterations: number): Promise<Record<string, unknown>> {
    return {
      loopId: "loop-1",
      projectId,
      researchGoal,
      status: "running",
      phase: "planning",
      currentIteration: 0,
      maxIterations,
      totalHypothesesTested: 0,
      totalExperimentsRun: 0,
      totalFindings: 0,
      iterations: [],
    };
  }

  async stopLoop(projectId: string, loopId: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      loopId,
      status: "stopped",
    };
  }

  async getLoop(projectId: string, loopId: string): Promise<Record<string, unknown>> {
    return {
      loopId,
      projectId,
      status: "running",
      phase: "executing",
      currentIteration: 3,
      maxIterations: 10,
      totalHypothesesTested: 2,
      totalExperimentsRun: 3,
      totalFindings: 1,
      iterations: [
        { iterationId: "iter-1", iterationNumber: 1, phase: "completed", hypothesis: "H7", experimentId: "E17", status: "completed" },
      ],
    };
  }

  async listLoops(projectId: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      loops: [
        { loopId: "loop-1", status: "running", currentIteration: 3 },
      ],
    };
  }


  async listSessions(projectId: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      count: 1,
      sessions: [
        {
          session_id: "sess-1",
          project_id: projectId,
          title: "Video behaviour study",
          research_goal: "Which conditions produce the most stable movement pattern?",
          status: "active",
          authoritative_runtime: "hybrid",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          loops: [{ loop_id: "loop-1", venue: "umrs", loop_role: "auto", attached_at: new Date().toISOString() }],
        },
      ],
    };
  }

  async createSession(projectId: string, researchGoal: string, title?: string): Promise<Record<string, unknown>> {
    return {
      session: {
        session_id: `sess-gen-${Math.random().toString(36).slice(2, 8)}`,
        project_id: projectId,
        title: title || researchGoal.slice(0, 64),
        research_goal: researchGoal,
        status: "active",
        authoritative_runtime: "umrs",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        loops: [],
      },
    };
  }

  async getSession(sessionId: string): Promise<Record<string, unknown>> {
    return {
      session: {
        session_id: sessionId,
        project_id: "demo-project",
        title: "Video behaviour study",
        research_goal: "Which conditions produce the most stable movement pattern?",
        status: "active",
        authoritative_runtime: "hybrid",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        loops: [{ loop_id: "loop-1", venue: "umrs", loop_role: "auto", attached_at: new Date().toISOString() }],
      },
    };
  }

  async correct(projectId: string, nodeId: string, correction: string): Promise<Record<string, unknown>> {
    return {
      projectId,
      nodeId,
      correction,
      status: "corrected",
    };
  }

  // ── ADR-031 server-spine fixtures (real wire shapes, demo mode only) ──

  async scientificCatalogs(projectId?: string): Promise<Record<string, unknown>> {
    // D-39 fixture: two version rows of one Scientific Catalog.
    return {
      catalogs: [
        {
          catalog_id: "scat-demo1",
          project_id: projectId ?? "demo-project",
          name: "mof-defects",
          version: "0.2.0",
          description: "MOF defect analysis catalog",
          ontology: "mof-v2",
          member_graph_ids: ["kg-defects"],
          skill_ids: ["skl-seg"],
          mcp_server_ids: ["mcp-microscope"],
          embedding_model_ids: ["intfloat/e5-base-v2"],
          contributors: ["alice"],
          licence: "CC-BY-4.0",
          status: "active",
          change_note: "added defect class D4",
        },
        {
          catalog_id: "scat-demo0",
          project_id: projectId ?? "demo-project",
          name: "mof-defects",
          version: "0.1.0",
          description: "MOF defect analysis catalog",
          ontology: "mof-v1",
          member_graph_ids: ["kg-defects"],
          skill_ids: [],
          mcp_server_ids: [],
          embedding_model_ids: ["intfloat/e5-base-v2"],
          contributors: ["alice"],
          licence: "CC-BY-4.0",
          status: "active",
          change_note: "first",
        },
      ],
    };
  }

  async catalogSearch(query: string): Promise<Record<string, unknown>> {
    return {
      query,
      project_id: "demo-project",
      results: [
        {
          s3_key: "demo-project/videos/session-tue-01.mp4",
          state: "ready",
          summary: "Tuesday session recording (condition A)",
          deterministic_summary: "mp4 1920x1080 00:12:40",
          detected_modality: "video",
          tags: ["session", "condition-a"],
          filename_signals: ["session", "tue"],
          relevance_score: 0.91,
          links: {
            document_id: "ragdoc-demo-vid12",
            link_method: "s3_object",
            chunk_nodes: 6,
            vector_rows: [{ model: "gte-multilingual-base", dimension: 768 }],
          },
        },
        {
          s3_key: "demo-project/notes/hypotheses.md",
          state: "ready",
          summary: "Session hypotheses H7/H8",
          deterministic_summary: "markdown 2.1 KiB",
          detected_modality: "text",
          tags: ["notes", "hypothesis"],
          filename_signals: ["hypotheses"],
          relevance_score: 0.77,
          links: null,
        },
      ],
    };
  }

  async catalogRead(s3Key: string): Promise<Record<string, unknown>> {
    return {
      s3_key: s3Key,
      offset: 0,
      length: 256,
      total_size: 2138,
      truncated: true,
      text: "# Session hypotheses\nH7: condition A stable\nH8: night more stable…",
      read_source: "extracted",
      extracted_text_ref: "demo-project/notes/hypotheses.md",
      meta: {},
      links: null,
    };
  }

  async catalogStats(): Promise<Record<string, unknown>> {
    return {
      project_id: "demo-project",
      object_count: 12,
      states: { ready: 10, pending: 2 },
      modalities: { video: 6, text: 3, structured: 3 },
      canonical_documents: 4,
      chunk_nodes: 18,
      vector_rows: 12,
    };
  }

  async knowledgeStatus(projectId: string): Promise<Record<string, unknown>> {
    return {
      project_id: projectId,
      artifacts_ingested: 6,
      artifacts_active: 5,
      reports: [],
      total_reports: 0,
      readiness_level: "partial",
      confidence: 0.6,
    };
  }

  async findings(projectId: string): Promise<Record<string, unknown>> {
    return {
      project_id: projectId,
      findings: [],
      total: 0,
      open_count: 0,
      resolved_count: 0,
      waived_count: 0,
    };
  }

  async graphs(projectId?: string): Promise<Record<string, unknown>> {
    return {
      graphs: [
        {
          graph_id: `g-${projectId ?? "demo-project"}-default`,
          name: projectId ?? "demo-project",
          description: `Default knowledge graph for project ${projectId ?? "demo-project"}`,
          owner_id: "",
          project_id: projectId ?? "demo-project",
          kind: "project",
          status: "active",
          provenance: {},
          created_at: new Date().toISOString(),
        },
      ],
    };
  }

  async graphContent(graphId: string): Promise<Record<string, unknown>> {
    return {
      graph_id: graphId,
      name: "demo-project",
      kind: "project",
      project_id: "demo-project",
      nodes: [
        { id: "H7", label: "H7: condition A stable", type: "hypothesis", project_id: "demo-project", graph_id: graphId, created_at: "" },
        { id: "YOLO-17", label: "YOLO-17 detection run", type: "experiment", project_id: "demo-project", graph_id: graphId, created_at: "" },
        { id: "VID-12", label: "Tuesday session video", type: "dataset", project_id: "demo-project", graph_id: graphId, created_at: "" },
        { id: "F8", label: "F8: nights less stable", type: "finding", project_id: "demo-project", graph_id: graphId, created_at: "" },
      ],
      edges: [
        { from: "H7", to: "YOLO-17", type: "tested_by", label: "tested_by", graph_id: graphId },
        { from: "YOLO-17", to: "VID-12", type: "derived_from", label: "derived_from", graph_id: graphId },
        { from: "YOLO-17", to: "F8", type: "produced", label: "produced", graph_id: graphId },
      ],
      truncated: false,
    };
  }

  async graphProposals(graphId: string): Promise<Record<string, unknown>> {
    return {
      proposals: [
        {
          proposal_id: "prop-1",
          graph_id: graphId,
          kind: "add_edge",
          status: "pending",
          created_at: new Date().toISOString(),
          actor: "agent",
          summary: "Agent proposes edge H8 tested_by YOLO-19 (night run queued)",
        },
      ],
    };
  }

  async graphDecide(proposalId: string, approved: boolean): Promise<Record<string, unknown>> {
    return { proposal_id: proposalId, status: approved ? "approved" : "rejected" };
  }

  async knowledgeQuery(projectId: string, query: string): Promise<Record<string, unknown>> {
    return {
      project_id: projectId,
      query,
      results: [
        {
          scope: "graph",
          node_id: "H7",
          label: "H7: condition A stable",
          lane: "canonical-graph",
          score: 1.0,
          metadata: { graph_id: `g-${projectId}-default`, graph_name: projectId },
        },
      ],
      wired_backends: ["graph", "rag"],
      unavailable_backends: [],
    };
  }

  async reconcile(projectId: string): Promise<Record<string, unknown>> {
    return {
      project_id: projectId,
      catalogue_available: true,
      counts: { COMPLETE: 3, EMBEDDING_PENDING: 1, PARTIAL: 1, ORPHANED: 0 },
      assets_sampled: 5,
      assets: [
        { document_id: "ragdoc-demo-vid12", state: "COMPLETE", reasons: [], chunk_nodes: 6, has_catalogue: true, has_embeddings: true },
        { document_id: "ragdoc-demo-h8", state: "EMBEDDING_PENDING", reasons: ["no embedding rows for chunks (non-error)"], chunk_nodes: 2, has_catalogue: true, has_embeddings: false },
        { document_id: "ragdoc-demo-old", state: "PARTIAL", reasons: ["catalogue row without canonical document"], chunk_nodes: 0, has_catalogue: true, has_embeddings: false },
      ],
      orphaned_vector_rows: 0,
      orphaned_chunk_nodes: 0,
    };
  }

  async lineage(artifactId: string): Promise<Record<string, unknown>> {
    return {
      artifact_id: artifactId,
      ancestors: [
        { artifact_id: "a2l-src-1", artifact_type: "a2l", title: "session upload batch 2026-09-01", edge_kind: "derived_from", created_at: new Date().toISOString() },
      ],
      descendants: [
        { artifact_id: "k-doc-9", artifact_type: "knowledge", title: "stability notes v2", edge_kind: "cites", created_at: new Date().toISOString() },
      ],
    };
  }
}
