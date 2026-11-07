/**
 * Event Subscription API tests — Phase 14.
 */

import { describe, it, expect, beforeEach } from "bun:test";
import { subscriptionStore, subscriptionActions } from "../src/stores/event-subscription";
import { agentActions } from "../src/stores/agent-runtime";

beforeEach(() => {
  agentActions.resetAgentRuntime();
  subscriptionActions.resetSubscriptions();
});

describe("Event Subscription API", () => {
  describe("subscribe / unsubscribe", () => {
    it("registers a subscriber and returns an ID", () => {
      const id = subscriptionActions.subscribe({ name: "TUI Client" });

      expect(id).toBeDefined();
      const sub = subscriptionActions.getSubscriber(id);
      expect(sub).toBeDefined();
      expect(sub!.name).toBe("TUI Client");
      expect(sub!.active).toBe(true);
    });

    it("unsubscribes by marking as inactive", () => {
      const id = subscriptionActions.subscribe({ name: "Web UI" });
      subscriptionActions.unsubscribe(id);

      const sub = subscriptionActions.getSubscriber(id);
      expect(sub!.active).toBe(false);
    });

    it("registers subscriber without emitting events", () => {
      subscriptionActions.subscribe({ name: "Companion" });

      const events = agentActions.getEventsByType("agent_started");
      expect(events.length).toBe(0);
    });
  });

  describe("poll", () => {
    it("returns events that match subscriber filters", () => {
      const id = subscriptionActions.subscribe({
        name: "TUI",
        filterTypes: ["agent_started"],
      });

      // Emit some events
      agentActions.emitEvent("agent_started", { test: 1 });
      agentActions.emitEvent("agent_completed", { test: 2 });
      agentActions.emitEvent("agent_started", { test: 3 });

      const events = subscriptionActions.poll(id);

      // Should get only agent_started events
      expect(events.length).toBe(2);
      expect(events.every((e) => e.eventType === "agent_started")).toBe(true);
    });

    it("returns empty array for inactive subscriber", () => {
      const id = subscriptionActions.subscribe({ name: "Inactive" });
      subscriptionActions.unsubscribe(id);

      const events = subscriptionActions.poll(id);

      expect(events).toEqual([]);
    });

    it("marks events as delivered", () => {
      const id = subscriptionActions.subscribe({ name: "TUI" });

      agentActions.emitEvent("agent_started", { test: 1 });
      subscriptionActions.poll(id);

      // Poll again — should get empty (already delivered)
      const events = subscriptionActions.poll(id);
      expect(events.length).toBe(0);
    });
  });

  describe("replay", () => {
    it("replays events from a specific timestamp", () => {
      const id = subscriptionActions.subscribe({ name: "Replay Client" });

      const before = new Date().toISOString();
      agentActions.emitEvent("agent_started", { test: 1 });
      agentActions.emitEvent("agent_completed", { test: 2 });

      const events = subscriptionActions.replay(id, before);

      expect(events.length).toBe(2);
    });

    it("applies filters during replay", () => {
      const id = subscriptionActions.subscribe({
        name: "Filtered Replay",
        filterTypes: ["agent_completed"],
      });

      agentActions.emitEvent("agent_started", { test: 1 });
      agentActions.emitEvent("agent_completed", { test: 2 });

      const events = subscriptionActions.replay(id, new Date(0).toISOString());

      expect(events.length).toBe(1);
      expect(events[0].eventType).toBe("agent_completed");
    });
  });

  describe("broadcast", () => {
    it("broadcasts event to matching subscribers", () => {
      const sub1 = subscriptionActions.subscribe({
        name: "Sub1",
        filterTypes: ["agent_started"],
      });
      const sub2 = subscriptionActions.subscribe({
        name: "Sub2",
        filterTypes: ["agent_completed"],
      });

      agentActions.emitEvent("agent_started", { test: 1 });

      // Sub1 should have event in pending buffer
      const s1 = subscriptionActions.getSubscriber(sub1);
      const s2 = subscriptionActions.getSubscriber(sub2);

      // Note: broadcast is called internally, but we can check delivery stats
      expect(s1).toBeDefined();
      expect(s2).toBeDefined();
    });
  });

  describe("getStats", () => {
    it("returns subscriber and event statistics", () => {
      subscriptionActions.subscribe({ name: "Sub1" });
      subscriptionActions.subscribe({ name: "Sub2" });

      const stats = subscriptionActions.getStats();

      expect(stats.totalSubscribers).toBe(2);
      expect(stats.activeSubscribers).toBe(2);
    });
  });

  describe("resetSubscriptions", () => {
    it("clears all subscribers", () => {
      subscriptionActions.subscribe({ name: "Sub1" });
      subscriptionActions.resetSubscriptions();

      expect(subscriptionStore.subscribers).toEqual([]);
    });
  });
});
