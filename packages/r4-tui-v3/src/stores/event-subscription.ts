/**
 * Event Subscription API — Phase 14.
 *
 * SSE-style event streaming for multi-client consistency.
 * TUI, Web UI, and Companion all see the same event stream.
 *
 * Features:
 * - Subscriber registration (each gets a unique ID)
 * - Event filtering by type, agent, project
 * - Replay from timestamp (for reconnecting clients)
 * - Backpressure handling (drop oldest if buffer full)
 *
 * Architectural Invariants:
 * - Event store is the single source of truth for events
 * - Subscribers are ephemeral (R4-owned)
 * - Event ordering is guaranteed per subscriber
 * - No event mutation — events are immutable once emitted
 */

import { createStore, produce } from "solid-js/store";
import { ulid } from "ulid";
import { agentRuntime, agentActions } from "./agent-runtime";
import type { ScientificEvent, ScientificEventType } from "../types";

// ── Subscriber Types ────────────────────────────────────────────────────

export interface EventSubscriber {
  id: string;
  name: string;
  // Filter: only receive these event types (empty = all)
  filterTypes: ScientificEventType[];
  // Filter: only receive events from this agent (undefined = all)
  filterAgentId?: string;
  // Last event ID delivered to this subscriber
  lastDeliveredEventId: string | null;
  // Buffer of pending events (not yet consumed by subscriber)
  pendingBuffer: ScientificEvent[];
  // Maximum buffer size before dropping oldest
  maxBufferSize: number;
  // Whether subscriber is active
  active: boolean;
  // Created at
  createdAt: string;
}

export interface EventSubscriptionStore {
  subscribers: EventSubscriber[];
  // Total events emitted
  totalEventsEmitted: number;
  // Total events delivered to subscribers
  totalEventsDelivered: number;
  // Events dropped due to buffer overflow
  totalEventsDropped: number;
}

const initialState: EventSubscriptionStore = {
  subscribers: [],
  totalEventsEmitted: 0,
  totalEventsDelivered: 0,
  totalEventsDropped: 0,
};

const [subscriptionStore, setSubscriptionStore] = createStore<EventSubscriptionStore>(initialState);

export { subscriptionStore };

// ── Event Subscription Actions ───────────────────────────────────────────

export const subscriptionActions = {
  /**
   * Register a new subscriber.
   * Returns the subscriber ID.
   */
  subscribe(options: {
    name: string;
    filterTypes?: ScientificEventType[];
    filterAgentId?: string;
    maxBufferSize?: number;
  }): string {
    const id = ulid();
    // Set lastDeliveredEventId to the current latest event, so the subscriber
    // only receives events emitted AFTER it subscribes
    const lastDeliveredEventId = agentRuntime.events[0]?.id ?? null;
    const subscriber: EventSubscriber = {
      id,
      name: options.name,
      filterTypes: options.filterTypes ?? [],
      filterAgentId: options.filterAgentId,
      lastDeliveredEventId,
      pendingBuffer: [],
      maxBufferSize: options.maxBufferSize ?? 100,
      active: true,
      createdAt: new Date().toISOString(),
    };

    setSubscriptionStore(produce((state) => {
      state.subscribers.push(subscriber);
    }));

    return id;
  },

  /**
   * Unsubscribe a subscriber by ID.
   */
  unsubscribe(subscriberId: string): void {
    setSubscriptionStore(produce((state) => {
      const sub = state.subscribers.find((s) => s.id === subscriberId);
      if (sub) sub.active = false;
    }));

    agentActions.emitEvent("agent_completed", {
      source: "event_subscription",
      action: "unsubscribe",
      subscriberId,
    });
  },

  /**
   * Get all active subscribers.
   */
  getActiveSubscribers(): EventSubscriber[] {
    return subscriptionStore.subscribers.filter((s) => s.active);
  },

  /**
   * Get a subscriber by ID.
   */
  getSubscriber(id: string): EventSubscriber | undefined {
    return subscriptionStore.subscribers.find((s) => s.id === id);
  },

  /**
   * Poll for new events for a subscriber.
   * Returns events that match the subscriber's filters and haven't been delivered yet.
   * Marks events as delivered.
   */
  poll(subscriberId: string): ScientificEvent[] {
    const subscriber = this.getSubscriber(subscriberId);
    if (!subscriber || !subscriber.active) return [];

    const allEvents = agentRuntime.events; // newest first

    // Determine which events to deliver (those newer than lastDeliveredEventId)
    let newEvents: ScientificEvent[];
    if (subscriber.lastDeliveredEventId) {
      const lastIdx = allEvents.findIndex((e) => e.id === subscriber.lastDeliveredEventId);
      if (lastIdx < 0) {
        // Last delivered event not found — deliver all
        newEvents = [...allEvents].reverse();
      } else {
        // Events at indices 0 to lastIdx-1 are newer than last delivered
        newEvents = allEvents.slice(0, lastIdx).reverse();
      }
    } else {
      // No last delivered event — deliver all
      newEvents = [...allEvents].reverse();
    }

    // Apply filters
    const filtered = newEvents.filter((e) => {
      if (subscriber.filterTypes.length > 0 && !subscriber.filterTypes.includes(e.eventType)) {
        return false;
      }
      if (subscriber.filterAgentId && e.agentId !== subscriber.filterAgentId) {
        return false;
      }
      return true;
    });

    // Mark as delivered — set lastDeliveredEventId to newest event overall
    if (allEvents.length > 0) {
      setSubscriptionStore(produce((state) => {
        const sub = state.subscribers.find((s) => s.id === subscriberId);
        if (sub) {
          sub.lastDeliveredEventId = allEvents[0].id; // newest event (index 0)
        }
      }));
      setSubscriptionStore("totalEventsDelivered", (n) => n + filtered.length);
    }

    return filtered;
  },

  /**
   * Replay events from a specific timestamp.
   * Useful for reconnecting clients that missed events.
   */
  replay(subscriberId: string, sinceTimestamp: string): ScientificEvent[] {
    const subscriber = this.getSubscriber(subscriberId);
    if (!subscriber || !subscriber.active) return [];

    const allEvents = agentRuntime.events;
    const replayEvents = allEvents.filter((e) => e.timestamp >= sinceTimestamp);

    // Apply filters
    const filtered = replayEvents.filter((e) => {
      if (subscriber.filterTypes.length > 0 && !subscriber.filterTypes.includes(e.eventType)) {
        return false;
      }
      if (subscriber.filterAgentId && e.agentId !== subscriber.filterAgentId) {
        return false;
      }
      return true;
    });

    return filtered;
  },

  /**
   * Broadcast an event to all matching subscribers.
   * Called internally after emitEvent.
   */
  broadcast(event: ScientificEvent): void {
    setSubscriptionStore(produce((state) => {
      for (const sub of state.subscribers) {
        if (!sub.active) continue;

        // Apply filters
        if (sub.filterTypes.length > 0 && !sub.filterTypes.includes(event.eventType)) {
          continue;
        }
        if (sub.filterAgentId && event.agentId !== sub.filterAgentId) {
          continue;
        }

        // Add to pending buffer
        sub.pendingBuffer.push(event);

        // Handle buffer overflow — drop oldest
        if (sub.pendingBuffer.length > sub.maxBufferSize) {
          sub.pendingBuffer.shift();
          state.totalEventsDropped++;
        }
      }
      state.totalEventsDelivered++;
    }));
  },

  /**
   * Get subscriber statistics.
   */
  getStats(): {
    totalSubscribers: number;
    activeSubscribers: number;
    totalEventsEmitted: number;
    totalEventsDelivered: number;
    totalEventsDropped: number;
  } {
    return {
      totalSubscribers: subscriptionStore.subscribers.length,
      activeSubscribers: this.getActiveSubscribers().length,
      totalEventsEmitted: subscriptionStore.totalEventsEmitted,
      totalEventsDelivered: subscriptionStore.totalEventsDelivered,
      totalEventsDropped: subscriptionStore.totalEventsDropped,
    };
  },

  /**
   * Reset the subscription store.
   */
  resetSubscriptions(): void {
    setSubscriptionStore(produce((state) => {
      state.subscribers = [];
      state.totalEventsEmitted = 0;
      state.totalEventsDelivered = 0;
      state.totalEventsDropped = 0;
    }));
  },
};
