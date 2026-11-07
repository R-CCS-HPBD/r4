/**
 * Scientific Blackboard — Phase 12.
 *
 * Agents do NOT talk directly to each other. They write to the
 * Blackboard and read from it. This is much more scalable than
 * direct agent-to-agent communication:
 *
 * - New agents can be added without modifying existing agents
 * - The Blackboard is auditable (every entry has provenance)
 * - The TUI can render the Blackboard as the central collaboration
 *   surface
 * - The Strategy Engine can read the entire Blackboard to make
 *   globally-informed decisions
 *
 * Architectural Invariants:
 * - Blackboard entries are ephemeral execution state (R4-owned)
 * - Scientific conclusions drawn from entries persist in UMRS
 * - Every entry has provenance (chain of entry IDs)
 * - Critiques are first-class — they can resolve or supersede entries
 */

import { createStore, produce } from "solid-js/store";
import { ulid } from "ulid";
import { agentActions, agentRuntime } from "./agent-runtime";
import type {
  BlackboardEntry,
  BlackboardEntryType,
  BlackboardEntryStatus,
  Critique,
  AgentRole,
} from "../types";

// ── Blackboard Store ─────────────────────────────────────────────────────

export interface BlackboardStore {
  entries: BlackboardEntry[];
  // Index by type for fast lookup
  entriesByType: Record<BlackboardEntryType, BlackboardEntry[]>;
}

const initialState: BlackboardStore = {
  entries: [],
  entriesByType: {
    observation: [],
    hypothesis: [],
    experiment: [],
    question: [],
    critique: [],
    finding: [],
    idea: [],
    review: [],
    publication: [],
  },
};

const [blackboardStore, setBlackboardStore] = createStore<BlackboardStore>(initialState);

export { blackboardStore };

// ── Blackboard Actions ──────────────────────────────────────────────────

export const blackboardActions = {
  /**
   * Write an entry to the Blackboard. The entry has provenance —
   * a chain of entry IDs that led to this one.
   */
  writeEntry(entry: {
    type: BlackboardEntryType;
    title: string;
    content: string;
    authorAgentId?: string;
    authorRole?: AgentRole;
    provenance?: string[];
    relatedHypothesisId?: string;
    relatedExperimentId?: string;
    confidence?: number;
    tags?: string[];
  }): string {
    const id = ulid();
    const now = new Date().toISOString();
    const newEntry: BlackboardEntry = {
      id,
      type: entry.type,
      status: "active",
      title: entry.title,
      content: entry.content,
      authorAgentId: entry.authorAgentId,
      authorRole: entry.authorRole,
      createdAt: now,
      updatedAt: now,
      provenance: entry.provenance ?? [],
      relatedHypothesisId: entry.relatedHypothesisId,
      relatedExperimentId: entry.relatedExperimentId,
      confidence: entry.confidence,
      critiques: [],
      tags: entry.tags ?? [],
    };

    setBlackboardStore(produce((state) => {
      state.entries.unshift(newEntry);
      state.entriesByType[entry.type].unshift(newEntry);
    }));

    agentActions.emitEvent("knowledge_created", {
      blackboardEntryId: id,
      type: entry.type,
      title: entry.title,
    });

    return id;
  },

  /**
   * Get a single entry by ID.
   */
  getEntry(id: string): BlackboardEntry | undefined {
    return blackboardStore.entries.find((e) => e.id === id);
  },

  /**
   * Query entries by type, author, or time range.
   */
  queryEntries(filters?: {
    type?: BlackboardEntryType;
    authorAgentId?: string;
    authorRole?: AgentRole;
    status?: BlackboardEntryStatus;
    relatedHypothesisId?: string;
    relatedExperimentId?: string;
    minConfidence?: number;
    tags?: string[];
  }): BlackboardEntry[] {
    const all = blackboardStore.entries;
    if (!filters) return all;

    return all.filter((e) => {
      if (filters.type && e.type !== filters.type) return false;
      if (filters.authorAgentId && e.authorAgentId !== filters.authorAgentId) return false;
      if (filters.authorRole && e.authorRole !== filters.authorRole) return false;
      if (filters.status && e.status !== filters.status) return false;
      if (filters.relatedHypothesisId && e.relatedHypothesisId !== filters.relatedHypothesisId) return false;
      if (filters.relatedExperimentId && e.relatedExperimentId !== filters.relatedExperimentId) return false;
      if (filters.minConfidence !== undefined && (e.confidence === undefined || e.confidence < filters.minConfidence)) return false;
      if (filters.tags && filters.tags.length > 0) {
        if (!filters.tags.some((t) => e.tags.includes(t))) return false;
      }
      return true;
    });
  },

  /**
   * Add a critique to an existing entry. Critiques are first-class —
   * they can resolve or supersede entries.
   */
  addCritique(entryId: string, critique: {
    content: string;
    criticAgentId?: string;
    criticRole?: AgentRole;
    severity: "info" | "warning" | "error";
  }): string {
    const entry = blackboardStore.entries.find((e) => e.id === entryId);
    if (!entry) {
      throw new Error(`Entry ${entryId} not found`);
    }

    const critiqueId = ulid();
    const newCritique: Critique = {
      id: critiqueId,
      entryId,
      content: critique.content,
      criticAgentId: critique.criticAgentId,
      criticRole: critique.criticRole,
      severity: critique.severity,
      createdAt: new Date().toISOString(),
      resolved: false,
    };

    setBlackboardStore(produce((state) => {
      const e = state.entries.find((en) => en.id === entryId);
      if (e) {
        e.critiques.push(newCritique);
        e.updatedAt = new Date().toISOString();
      }
    }));

    agentActions.emitEvent("knowledge_updated", {
      entryId,
      critiqueId,
      severity: critique.severity,
    });

    return critiqueId;
  },

  /**
   * Resolve a critique. Resolved critiques are kept for audit.
   */
  resolveCritique(critiqueId: string): void {
    setBlackboardStore(produce((state) => {
      for (const entry of state.entries) {
        const critique = entry.critiques.find((c) => c.id === critiqueId);
        if (critique) {
          critique.resolved = true;
          critique.resolvedAt = new Date().toISOString();
          entry.updatedAt = new Date().toISOString();
          break;
        }
      }
    }));
  },

  /**
   * Update an entry's status (e.g., mark as resolved, superseded).
   */
  updateEntryStatus(entryId: string, status: BlackboardEntryStatus): void {
    setBlackboardStore(produce((state) => {
      const entry = state.entries.find((e) => e.id === entryId);
      if (entry) {
        entry.status = status;
        entry.updatedAt = new Date().toISOString();
      }
    }));
  },

  /**
   * Get entries that have unresolved critiques — these are candidates
   * for the Reviewer agent to address.
   */
  getEntriesWithUnresolvedCritiques(): BlackboardEntry[] {
    return blackboardStore.entries.filter((e) =>
      e.critiques.some((c) => !c.resolved),
    );
  },

  /**
   * Get the provenance chain for an entry — the full tree of entries
   * that led to this one.
   */
  getProvenanceChain(entryId: string): BlackboardEntry[] {
    const visited = new Set<string>();
    const chain: BlackboardEntry[] = [];

    const walk = (id: string) => {
      if (visited.has(id)) return;
      visited.add(id);
      const entry = blackboardStore.entries.find((e) => e.id === id);
      if (entry) {
        chain.push(entry);
        for (const parentId of entry.provenance) {
          walk(parentId);
        }
      }
    };

    walk(entryId);
    return chain;
  },

  /**
   * Get all entries written by a specific agent.
   */
  getEntriesByAgent(agentId: string): BlackboardEntry[] {
    return blackboardStore.entries.filter((e) => e.authorAgentId === agentId);
  },

  /**
   * Reset the blackboard store.
   */
  resetBlackboard(): void {
    setBlackboardStore(produce((state) => {
      state.entries = [];
      state.entriesByType = {
        observation: [],
        hypothesis: [],
        experiment: [],
        question: [],
        critique: [],
        finding: [],
        idea: [],
        review: [],
        publication: [],
      };
    }));
  },
};
