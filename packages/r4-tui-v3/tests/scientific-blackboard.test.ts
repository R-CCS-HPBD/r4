/**
 * Scientific Blackboard tests — Phase 12.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { blackboardStore, blackboardActions } from "../src/stores/scientific-blackboard";
import { agentActions } from "../src/stores/agent-runtime";

beforeEach(() => {
  agentActions.resetAgentRuntime();
  blackboardActions.resetBlackboard();
});

describe("Scientific Blackboard", () => {
  describe("writeEntry", () => {
    it("writes an entry and assigns provenance", () => {
      const id = blackboardActions.writeEntry({
        type: "observation",
        title: "Paper P23 reports correlation",
        content: "GCMC MOF pore size correlates with CO2 uptake",
        authorRole: "literature",
        tags: ["MOF", "CO2"],
      });

      const entry = blackboardActions.getEntry(id);
      expect(entry).toBeDefined();
      expect(entry!.type).toBe("observation");
      expect(entry!.status).toBe("active");
      expect(entry!.tags).toContain("MOF");
    });

    it("emits knowledge_created event", () => {
      blackboardActions.writeEntry({
        type: "hypothesis",
        title: "H7",
        content: "Methodology X will yield result Y",
      });

      const events = agentActions.getEventsByType("knowledge_created");
      expect(events.length).toBe(1);
    });

    it("accepts provenance chain from parent entries", () => {
      const parentId = blackboardActions.writeEntry({
        type: "observation",
        title: "Parent observation",
        content: "Base observation",
      });

      const childId = blackboardActions.writeEntry({
        type: "hypothesis",
        title: "Derived hypothesis",
        content: "Derived from parent",
        provenance: [parentId],
      });

      const chain = blackboardActions.getProvenanceChain(childId);
      expect(chain.length).toBe(2);
      expect(chain.some((e) => e.id === parentId)).toBe(true);
    });
  });

  describe("queryEntries", () => {
    it("filters entries by type", () => {
      blackboardActions.writeEntry({
        type: "observation",
        title: "Obs 1",
        content: "Observation",
      });
      blackboardActions.writeEntry({
        type: "hypothesis",
        title: "Hyp 1",
        content: "Hypothesis",
      });

      const observations = blackboardActions.queryEntries({ type: "observation" });
      expect(observations.length).toBe(1);
      expect(observations[0].type).toBe("observation");
    });

    it("filters entries by minimum confidence", () => {
      blackboardActions.writeEntry({
        type: "finding",
        title: "High confidence finding",
        content: "Strong evidence",
        confidence: 0.9,
      });
      blackboardActions.writeEntry({
        type: "finding",
        title: "Low confidence finding",
        content: "Weak evidence",
        confidence: 0.3,
      });

      const highConf = blackboardActions.queryEntries({ minConfidence: 0.7 });
      expect(highConf.length).toBe(1);
    });

    it("filters entries by tags", () => {
      blackboardActions.writeEntry({
        type: "idea",
        title: "Tagged idea",
        content: "Idea with tags",
        tags: ["MOF", "experimental"],
      });
      blackboardActions.writeEntry({
        type: "idea",
        title: "Untagged idea",
        content: "Idea without tags",
      });

      const tagged = blackboardActions.queryEntries({ tags: ["MOF"] });
      expect(tagged.length).toBe(1);
    });
  });

  describe("addCritique", () => {
    it("adds critique to an existing entry", () => {
      const entryId = blackboardActions.writeEntry({
        type: "hypothesis",
        title: "H7",
        content: "Methodology X will yield result Y",
      });

      const critiqueId = blackboardActions.addCritique(entryId, {
        content: "Sample size too small",
        severity: "warning",
      });

      const entry = blackboardActions.getEntry(entryId);
      expect(entry!.critiques.length).toBe(1);
      expect(entry!.critiques[0].id).toBe(critiqueId);
      expect(entry!.critiques[0].resolved).toBe(false);
    });

    it("throws when entry does not exist", () => {
      expect(() =>
        blackboardActions.addCritique("nonexistent", {
          content: "Critique",
          severity: "info",
        }),
      ).toThrow();
    });

    it("emits knowledge_updated event", () => {
      const entryId = blackboardActions.writeEntry({
        type: "hypothesis",
        title: "H7",
        content: "Test",
      });

      blackboardActions.addCritique(entryId, {
        content: "Critique",
        severity: "info",
      });

      const events = agentActions.getEventsByType("knowledge_updated");
      expect(events.length).toBe(1);
    });
  });

  describe("resolveCritique", () => {
    it("marks critique as resolved with timestamp", () => {
      const entryId = blackboardActions.writeEntry({
        type: "hypothesis",
        title: "H7",
        content: "Test",
      });

      const critiqueId = blackboardActions.addCritique(entryId, {
        content: "Critique",
        severity: "warning",
      });

      blackboardActions.resolveCritique(critiqueId);

      const entry = blackboardActions.getEntry(entryId);
      expect(entry!.critiques[0].resolved).toBe(true);
      expect(entry!.critiques[0].resolvedAt).toBeDefined();
    });
  });

  describe("getEntriesWithUnresolvedCritiques", () => {
    it("returns only entries with unresolved critiques", () => {
      const entry1Id = blackboardActions.writeEntry({
        type: "hypothesis",
        title: "H7",
        content: "Test 1",
      });
      const entry2Id = blackboardActions.writeEntry({
        type: "hypothesis",
        title: "H8",
        content: "Test 2",
      });

      const critique1Id = blackboardActions.addCritique(entry1Id, {
        content: "Unresolved",
        severity: "warning",
      });
      blackboardActions.addCritique(entry2Id, {
        content: "Will be resolved",
        severity: "warning",
      });

      blackboardActions.resolveCritique(critique1Id);

      const withUnresolved = blackboardActions.getEntriesWithUnresolvedCritiques();
      expect(withUnresolved.length).toBe(1);
      expect(withUnresolved[0].id).toBe(entry2Id);
    });
  });

  describe("updateEntryStatus", () => {
    it("updates entry status", () => {
      const entryId = blackboardActions.writeEntry({
        type: "hypothesis",
        title: "H7",
        content: "Test",
      });

      blackboardActions.updateEntryStatus(entryId, "resolved");

      const entry = blackboardActions.getEntry(entryId);
      expect(entry!.status).toBe("resolved");
    });
  });

  describe("getEntriesByAgent", () => {
    it("returns entries written by a specific agent", () => {
      const agentId = "agent-001";
      blackboardActions.writeEntry({
        type: "observation",
        title: "Agent observation",
        content: "By agent-001",
        authorAgentId: agentId,
      });
      blackboardActions.writeEntry({
        type: "observation",
        title: "Other observation",
        content: "By another agent",
        authorAgentId: "agent-002",
      });

      const entries = blackboardActions.getEntriesByAgent(agentId);
      expect(entries.length).toBe(1);
      expect(entries[0].authorAgentId).toBe(agentId);
    });
  });
});
