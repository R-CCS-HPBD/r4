/**
 * Knowledge Store — Phase 13 UI Integration.
 *
 * Manages the state of the UMRS knowledge modules (Phases 3-12)
 * for the TUI. The store is a thin wrapper around the KnowledgeClient
 * — it does not duplicate state machines, registries, or retry semantics.
 *
 * The store provides:
 * - Ingestion state (Phase 3)
 * - Extraction state (Phase 4)
 * - Verification state (Phase 6)
 * - Coverage state (Phase 7)
 * - Memory state (Phase 9)
 * - Readiness state (Phase 10)
 * - Research plan state (Phase 11)
 * - Research loop state (Phase 12)
 *
 * Architectural Invariants:
 * - UMRS owns ALL scientific knowledge
 * - R4 owns orchestration only
 * - The store is ephemeral (R4-owned)
 * - All data comes from the KnowledgeClient (which proxies through Gateway)
 */

import { createStore } from "solid-js/store";
import type {
  IngestionResult,
  ExtractionResult,
  VerificationResult,
  CoverageReport,
  MemorySnapshot,
  ReadinessAssessment,
  ResearchPlan,
  ResearchLoop,
  ResearchSession,
} from "../types";

// ── Store Interface ──────────────────────────────────────────────────────

export interface KnowledgeStore {
  // Phase 3: Ingestion
  ingestion: IngestionResult | null;
  // Phase 4: Extraction
  extraction: ExtractionResult | null;
  // Phase 6: Verification
  verification: VerificationResult | null;
  // Phase 7: Coverage
  coverage: CoverageReport | null;
  // Phase 9: Memory
  memory: MemorySnapshot | null;
  // Phase 10: Readiness
  readiness: ReadinessAssessment | null;
  // Phase 11: Research plan
  plan: ResearchPlan | null;
  // Phase 12: Research loop
  loop: ResearchLoop | null;
  // ADR-025: durable research sessions attached to the current project
  sessions: ResearchSession[] | null;
  // Loading state
  loading: boolean;
  // Error state
  error: string | null;
  /** K1.3 review (2026-10-24): the server list could not be read (the pane
   * keeps its last rows and says so, instead of showing "no knowledge"). */
  listError: string | null;
}

const initialState: KnowledgeStore = {
  ingestion: null,
  extraction: null,
  verification: null,
  coverage: null,
  memory: null,
  readiness: null,
  plan: null,
  loop: null,
  sessions: null,
  loading: false,
  error: null,
  listError: null,
};

const [knowledgeStore, setKnowledgeStore] = createStore<KnowledgeStore>(initialState);

export { knowledgeStore };

// ── Knowledge Actions ────────────────────────────────────────────────────

export const knowledgeActions = {
  setIngestion(result: IngestionResult | null): void {
    setKnowledgeStore("ingestion", result);
  },

  setExtraction(result: ExtractionResult | null): void {
    setKnowledgeStore("extraction", result);
  },

  setVerification(result: VerificationResult | null): void {
    setKnowledgeStore("verification", result);
  },

  setCoverage(result: CoverageReport | null): void {
    setKnowledgeStore("coverage", result);
  },

  setMemory(result: MemorySnapshot | null): void {
    setKnowledgeStore("memory", result);
  },

  setReadiness(result: ReadinessAssessment | null): void {
    setKnowledgeStore("readiness", result);
  },

  setPlan(result: ResearchPlan | null): void {
    setKnowledgeStore("plan", result);
  },

  setLoop(result: ResearchLoop | null): void {
    setKnowledgeStore("loop", result);
  },

  setSessions(result: ResearchSession[] | null): void {
    setKnowledgeStore("sessions", result);
  },

  setLoading(loading: boolean): void {
    setKnowledgeStore("loading", loading);
  },

  setError(error: string | null): void {
    setKnowledgeStore("error", error);
  },

  setListError(error: string | null): void {
    setKnowledgeStore("listError", error);
  },

  reset(): void {
    setKnowledgeStore(initialState);
  },
};
