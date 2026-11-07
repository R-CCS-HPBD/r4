/**
 * TUI audit r2 (2026-10-17), hooks lane — #45/#52/#56 consumer side: a poll
 * that answered with per-probe `problems` is DEGRADED (UMRS/HPC chips,
 * backend error, /health), never "connected"; a clean poll stays connected.
 * Review follow-up: the gateway chip stays connected on a partial poll (it
 * answered; status-bar gates the identity-circle refresh on it), and a
 * recovery after an outage re-announces a still-present problem set instead
 * of a bare "reconnected".
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { createRoot } from "solid-js";
import { createBackendPoller, stopBackendPoller } from "../src/hooks/useBackend";
import { R4PlatformAdapter } from "../src/adapters/platform";
import type { BackendSnapshot, DataSource } from "../src/adapters/types";
import { resetWorkspace, workspace } from "../src/stores";
import { setDemoMode } from "../src/lib/console-mode";

let snapshot: BackendSnapshot = { endpoints: [], models: [], jobs: [] };
let outage = false;

const source = {
  name: "r4-cli",
  loadWorkspace: async () => ({ project: null }),
  pollBackend: async () => {
    if (outage) throw new Error("backend unreachable — endpoints: exit 1");
    return snapshot;
  },
  chat: async () => "",
} as unknown as DataSource;

const waitFor = async (cond: () => boolean) => {
  for (let i = 0; i < 100 && !cond(); i++) await new Promise((r) => setTimeout(r, 5));
};

beforeEach(() => {
  setDemoMode(false);
  resetWorkspace();
  snapshot = { endpoints: [], models: [], jobs: [] };
  outage = false;
});

const titles = () => workspace.notifications.map((n) => n.title);

afterEach(() => stopBackendPoller());

describe("backend poller with per-probe problems", () => {
  it("marks the UMRS/HPC channels degraded, keeps the gateway connected, and names the problem", async () => {
    snapshot = { endpoints: [], models: [], jobs: [], problems: ["endpoints (plat): 401 — run r4 login"] };
    createRoot(() => createBackendPoller(source, 5));
    await waitFor(() => workspace.connection.umrs !== "connecting");
    expect(workspace.connection.gateway).toBe("connected");
    expect(workspace.connection.umrs).toBe("degraded");
    expect(workspace.connection.hpc).toBe("degraded");
    expect(workspace.backendError).toContain("run r4 login");
  });

  it("a clean poll stays connected", async () => {
    createRoot(() => createBackendPoller(source, 5));
    await waitFor(() => workspace.connection.gateway !== "connecting");
    expect(workspace.connection.gateway).toBe("connected");
  });

  it("recovery after an outage re-announces a still-present problem set, not 'reconnected'", async () => {
    snapshot = { endpoints: [], models: [], jobs: [], problems: ["models: exit 1"] };
    createRoot(() => createBackendPoller(source, 5));
    await waitFor(() => titles().includes("Backend partly unreachable"));
    outage = true;
    await waitFor(() => titles().includes("Backend unreachable"));
    outage = false;
    await waitFor(() => workspace.connection.umrs === "degraded" && workspace.backendError === "models: exit 1");
    await new Promise((r) => setTimeout(r, 30));
    expect(titles().filter((t) => t === "Backend partly unreachable")).toHaveLength(2);
    expect(titles()).not.toContain("Backend reconnected");
  });

  it("a clean recovery after an outage says 'reconnected'", async () => {
    outage = true;
    createRoot(() => createBackendPoller(source, 5));
    await waitFor(() => titles().includes("Backend unreachable"));
    outage = false;
    await waitFor(() => titles().includes("Backend reconnected"));
    expect(workspace.connection.gateway).toBe("connected");
  });

  it("/health reports degraded for a partial poll", async () => {
    snapshot = { endpoints: [], models: [], jobs: [], problems: ["models: exit 1"] };
    expect(await new R4PlatformAdapter(source).healthCheck()).toBe("degraded");
    snapshot = { endpoints: [], models: [], jobs: [] };
    expect(await new R4PlatformAdapter(source).healthCheck()).toBe("good");
  });
});
