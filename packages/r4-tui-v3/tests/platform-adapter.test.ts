/**
 * Platform adapter tests.
 *
 * Tests the platform abstraction layer that decouples stores from
 * concrete backend implementations (r4-cli, offline, remote).
 */

import { describe, it, expect, beforeEach } from "bun:test";
import {
  createPlatformAdapter,
  inferPlatformKind,
  R4PlatformAdapter,
} from "../src/adapters/platform";
import type { PlatformAdapter, PlatformConfig } from "../src/types/platform";
import type { DataSource } from "../src/adapters/types";
import { setDemoMode } from "../src/lib/console-mode";
import { actions, workspace, resetWorkspace } from "../src/stores";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";

// ── Mock DataSource ──────────────────────────────────────────────────────

function createMockSource(name: string, opts?: { streaming?: boolean }): DataSource {
  return {
    name,
    async loadWorkspace() {
      return {
        project: null,
        goals: [],
        tasks: [],
        knowledge: [],
        agents: [],
        notifications: [],
        events: [],
        connection: {},
      };
    },
    async pollBackend() {
      return { endpoints: [], models: [], jobs: [] };
    },
    async chat(_message: string) {
      return "mock reply";
    },
    chatStream: opts?.streaming
      ? async function* () {
          yield { delta: "hello" };
          yield { done: true };
        }
      : undefined,
    provisionEndpoint: async () => ({ ok: true, message: "mock provision" }),
    stopEndpoint: async () => ({ ok: true, message: "mock stop" }),
    cancelJob: async () => ({ ok: true, message: "mock cancel" }),
    retryJob: async () => ({ ok: true, message: "mock retry" }),
    getJobLogs: async () => "mock logs",
  };
}

describe("platform adapter", () => {
  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    resetWorkspace();
    actions.closeAllOverlays();
  });

  describe("createPlatformAdapter", () => {
    it("creates an adapter from r4-cli source", () => {
      const source = createMockSource("r4-cli", { streaming: true });
      const adapter = createPlatformAdapter(source);
      expect(adapter).toBeInstanceOf(R4PlatformAdapter);
      // The adapter wraps the source; verify via getConfig
      expect(adapter.getConfig).toBeDefined();
    });

    it("creates an adapter from offline source", () => {
      const source = createMockSource("offline");
      const adapter = createPlatformAdapter(source);
      expect(adapter).toBeInstanceOf(R4PlatformAdapter);
    });

    it("infers platform kind correctly", () => {
      expect(inferPlatformKind("r4-cli")).toBe("cli");
      expect(inferPlatformKind("offline")).toBe("offline");
      expect(inferPlatformKind("remote")).toBe("embedded"); // No explicit remote case, defaults to embedded
      expect(inferPlatformKind("unknown")).toBe("embedded");
    });
  });

  describe("getConfig", () => {
    it("returns platform config with correct fields", async () => {
      const source = createMockSource("r4-cli", { streaming: true });
      const adapter = createPlatformAdapter(source);
      const config = await adapter.getConfig();
      expect(config.name).toBe("r4-cli");
      expect(config.sessionId).toBeTruthy();
      expect(config.startedAt).toBeTruthy();
      expect(config.healthy).toBe(true);
      expect(config.quality).toBe("good");
      expect(config.readonly).toBe(false);
      expect(config.capabilities.streamingChat).toBe(true);
      expect(config.capabilities.endpointProvisioning).toBe(true);
    });

    it("marks offline as readonly", async () => {
      const source = createMockSource("offline");
      const adapter = createPlatformAdapter(source);
      const config = await adapter.getConfig();
      expect(config.readonly).toBe(true);
    });

    it("caches config after first call", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const config1 = await adapter.getConfig();
      const config2 = await adapter.getConfig();
      expect(config1).toBe(config2); // Same object (cached)
    });
  });

  describe("healthCheck", () => {
    it("returns good when backend is reachable", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const quality = await adapter.healthCheck();
      expect(quality).toBe("good");
    });
  });

  describe("workspace/load", () => {
    it("delegates loadWorkspace to source", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const snapshot = await adapter.loadWorkspace();
      expect(snapshot).toBeDefined();
      expect(snapshot.project).toBeNull();
      expect(snapshot.goals).toEqual([]);
    });
  });

  describe("chat", () => {
    it("delegates chat to source", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const reply = await adapter.chat("hello");
      expect(reply).toBe("mock reply");
    });

    it("streams when source supports it", async () => {
      const source = createMockSource("r4-cli", { streaming: true });
      const adapter = createPlatformAdapter(source);
      const chunks: string[] = [];
      if (adapter.chatStream) {
        for await (const chunk of adapter.chatStream!("hello")) {
          if (chunk.delta) chunks.push(chunk.delta);
        }
      }
      expect(chunks).toContain("hello");
    });
  });

  describe("backend operations", () => {
    it("provisionEndpoint delegates to source", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const result = await adapter.provisionEndpoint!("test-model");
      expect(result.ok).toBe(true);
      expect(result.message).toContain("mock provision");
    });

    it("stopEndpoint delegates to source", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const result = await adapter.stopEndpoint!("ep-1");
      expect(result.ok).toBe(true);
    });

    it("cancelJob delegates to source", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const result = await adapter.cancelJob!("job-1");
      expect(result.ok).toBe(true);
    });

    it("retryJob delegates to source", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const result = await adapter.retryJob!("job-2");
      expect(result.ok).toBe(true);
    });

    it("getJobLogs delegates to source", async () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      const logs = await adapter.getJobLogs!("job-3");
      expect(logs).toBe("mock logs");
    });
  });

  describe("integration with stores", () => {
    it("creates and returns adapter", () => {
      const source = createMockSource("r4-cli");
      const adapter = createPlatformAdapter(source);
      expect(adapter).toBeDefined();
      expect(typeof adapter.getConfig).toBe("function");
    });

    it("workspace has no source initially", () => {
      expect(workspace.dataSource == null).toBe(true);
    });
  });
});
