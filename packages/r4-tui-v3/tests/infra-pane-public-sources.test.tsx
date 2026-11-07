/**
 * Infrastructure pane, public sources (maintainer report 2026-10-25, wave
 * 82): "public endpoint is indicated as just 'unknown' and no available
 * models are listed". `r4 endpoint list --json --all` lists each configured
 * public source as one `instance_id: "public"` row with `model: null` and
 * `status: "configured"`; the mapper's "unknown" reached the Endpoints
 * column and the Models panel, which built every section from the endpoint
 * feed only and so listed no public model. Rows below are the live shapes.
 */
import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { testRender } from "@opentui/solid";
import { App } from "../src/app";
import { mapEndpoint, mapModel } from "../src/adapters/mappers";
import { modelSections } from "../src/components/models-panel";
import { endpointLabel, endpointStateText } from "../src/lib/endpoint-label";
import { infraAvailableActionsLine } from "../src/lib/space-shortcuts";
import { actions, resetWorkspace, setWorkspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { setTeamsState } from "../src/stores/teams";
import { scienceActions } from "../src/stores/science";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { resetTabsRegistryForTests } from "../src/tabs/registry";

const RIKYU_URL = "https://api.rikyu.r-ccs.riken.jp/v1";

function publicRow(source: string) {
  return {
    instance_id: "public",
    model: null,
    status: "configured",
    endpoint_url: RIKYU_URL,
    _source: source,
    tier: "public",
    backend: null,
  };
}

const ENDPOINT_ROWS = [
  { instance_id: "ep-bdrt-1", model: "glm-5.3-flash", status: "ready", _source: "bdrt", tier: "project", host: "h1", port: 8223 },
  { instance_id: "ep-cloud-1", model: "qwen3.6-35b", status: "failed", _source: "rccs-cloud", tier: "project", host: "h2", port: 8000 },
  { instance_id: "ep-sa-1", model: "gpt-oss-20b", status: "ready", _source: "standalone", tier: "user", host: "h3", port: 9000 },
  publicRow("rikyu"),
  publicRow("canary-v1"),
  publicRow("canary-v2"),
];

const MODEL_ROWS = [
  { name: "glm-5.3", source: "rikyu", kind: "public" },
  { name: "kimi-k3", source: "rikyu", kind: "public" },
  { name: "glm-5.3-flash", source: "bdrt", kind: "platform" },
  // A platform model with no endpoint row: platform sections stay on the
  // live endpoint feed (b4646d8d), so it must not appear.
  { name: "llama-4-maverick", source: "bdrt", kind: "platform" },
  { name: "qwen3.6-35b", source: "rccs-cloud", kind: "platform" },
  { name: "gpt-oss-20b", source: "standalone", kind: "platform" },
];

const endpoints = () => ENDPOINT_ROWS.map((row) => mapEndpoint(row as never));
const models = () => MODEL_ROWS.map((row) => mapModel(row as never));

describe("public-source endpoint rows (mapper + label)", () => {
  it("marks the configured public row and labels it source · public · host", () => {
    const ep = mapEndpoint(publicRow("rikyu") as never);
    expect(ep.isPublic).toBe(true);
    expect(endpointLabel(ep)).toBe("rikyu · public · api.rikyu.r-ccs.riken.jp");
    expect(endpointStateText(ep)).toBe("configured");
  });

  it("leaves platform rows as before", () => {
    const ep = mapEndpoint(ENDPOINT_ROWS[0] as never);
    expect(ep.isPublic).toBeUndefined();
    expect(endpointLabel(ep)).toBe("glm-5.3-flash");
    expect(endpointStateText(ep)).toBe("ready");
  });

  it("keeps a model row's source and kind", () => {
    const m = mapModel({ name: "glm-5.3", source: "rikyu", kind: "public" } as never);
    expect(m.source).toBe("rikyu");
    expect(m.kind).toBe("public");
  });

  it("the infra actions line names a public source, not 'already stopped'", () => {
    resetWorkspace();
    setWorkspace("endpoints", [mapEndpoint(publicRow("rikyu") as never)]);
    actions.selectObject("public");
    const line = infraAvailableActionsLine();
    expect(line).toContain("Public source · rikyu · public");
    expect(line).not.toContain("already stopped");
    resetWorkspace();
  });
});

describe("modelSections", () => {
  const byName = () => new Map(modelSections(endpoints(), models()).map((s) => [s.name, s]));

  it("lists a public source's own models as available, no unknown entry", () => {
    const rikyu = byName().get("rikyu")!;
    expect(rikyu.isPublic).toBe(true);
    expect(rikyu.entries.map((e) => [e.model, e.state])).toEqual([
      ["glm-5.3", "ready"],
      ["kimi-k3", "ready"],
    ]);
  });

  it("a public source with no listed models has an empty section", () => {
    for (const name of ["canary-v1", "canary-v2"]) {
      const section = byName().get(name)!;
      expect(section.isPublic).toBe(true);
      expect(section.entries).toEqual([]);
    }
  });

  it("platform sections keep the endpoint feed, filtered by source", () => {
    const sections = byName();
    expect(sections.get("bdrt")!.entries.map((e) => e.model)).toEqual(["glm-5.3-flash"]);
    expect(sections.get("rccs-cloud")!.entries.map((e) => [e.model, e.state])).toEqual([["qwen3.6-35b", "other"]]);
    expect(sections.get("standalone")!.entries.map((e) => e.model)).toEqual(["gpt-oss-20b"]);
  });

  it("no section carries an unknown model and platform sections come first", () => {
    const sections = modelSections(endpoints(), models());
    expect(sections.flatMap((s) => s.entries.map((e) => e.model))).not.toContain("unknown");
    expect(sections.map((s) => s.isPublic)).toEqual([false, false, false, true, true, true]);
  });
});

/* One registered source (or a legacy `endpoint_url`): the CLI's short paths.
 * `endpoint list` → `_print_public_endpoint_list` (the row names the source's
 * default model, or `cfg.model` with no `_source`); `model list --json` →
 * `_cmd_model_list_public` / `_emit_model_list_json`: `{model_id, name,
 * source: <provider label>}`, NO `kind`, the label not the source name. */
const SINGLE_SOURCE_ENDPOINT = { ...publicRow("rikyu"), model: "glm-5.3" };
const SINGLE_SOURCE_MODELS = [
  { model_id: "glm-5.3", name: "glm-5.3", source: "public" },
  { model_id: "kimi-k3", name: "kimi-k3", source: "public" },
];
const LEGACY_ENDPOINT = {
  instance_id: "public",
  tier: "public",
  model: "rivault-gpt-oss-static",
  status: "configured",
  endpoint_url: "https://api.openai.com/v1",
};
const LEGACY_MODELS = [
  { model_id: "gpt-5", name: "gpt-5", source: "openai" },
  // Platform registry rows on the same listing never reach a public section.
  { model_id: "m-1", name: "registry-model", source: "platform", scope: "project" },
];

describe("modelSections, single public source and legacy endpoint_url", () => {
  it("one source: kind-less model rows fill that source's section", () => {
    const sections = modelSections(
      [mapEndpoint(SINGLE_SOURCE_ENDPOINT as never)],
      SINGLE_SOURCE_MODELS.map((row) => mapModel(row as never)),
    );
    expect(sections.map((s) => [s.name, s.isPublic])).toEqual([["rikyu", true]]);
    expect(sections[0]!.entries.map((e) => [e.model, e.state])).toEqual([
      ["glm-5.3", "ready"],
      ["kimi-k3", "ready"],
    ]);
  });

  it("one source with no model listing still shows the row's own default model", () => {
    const sections = modelSections([mapEndpoint(SINGLE_SOURCE_ENDPOINT as never)], []);
    expect(sections[0]!.entries.map((e) => e.model)).toEqual(["glm-5.3"]);
  });

  it("legacy endpoint_url row: filed under its host, never 'unknown'; cfg.model not offered", () => {
    const ep = mapEndpoint(LEGACY_ENDPOINT as never);
    expect(endpointLabel(ep)).toBe("public · api.openai.com");
    const sections = modelSections([ep], LEGACY_MODELS.map((row) => mapModel(row as never)));
    expect(sections.map((s) => [s.name, s.isPublic])).toEqual([["api.openai.com", true]]);
    expect(sections[0]!.entries.map((e) => e.model)).toEqual(["gpt-5"]);
  });

  it("kind-less rows are not guessed into one of several public sections", () => {
    const sections = modelSections(endpoints(), SINGLE_SOURCE_MODELS.map((row) => mapModel(row as never)));
    for (const name of ["rikyu", "canary-v1", "canary-v2"]) {
      expect(sections.find((s) => s.name === name)!.entries).toEqual([]);
    }
  });
});

type Setup = Awaited<ReturnType<typeof testRender>>;

const savedBackend = process.env.R4_TUI_BACKEND;

function reset(): void {
  stopBackendPoller();
  stopKnowledgePoller();
  agentActions.resetAgentRuntime();
  resetWorkspace();
  resetTabsRegistryForTests();
  setTeamsState({ sessions: [], status: {}, live: {}, loading: false, error: null });
  actions.closeAllOverlays();
}

async function settle(setup: Setup, ms = 60): Promise<void> {
  await new Promise((r) => setTimeout(r, ms));
  await setup.renderOnce();
}

describe("infrastructure pane renders public sources", () => {
  beforeEach(() => {
    process.env.R4_TUI_BACKEND = "offline";
    reset();
  });

  afterEach(async () => {
    await new Promise((r) => setTimeout(r, 50));
    scienceActions.reset();
    reset();
    if (savedBackend === undefined) delete process.env.R4_TUI_BACKEND;
    else process.env.R4_TUI_BACKEND = savedBackend;
  });

  it("shows source · public and the public models, never 'unknown'", async () => {
    const setup = await testRender(() => <App />, { width: 260, height: 60 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await settle(setup);
    setWorkspace("endpoints", endpoints());
    setWorkspace("models", models());
    actions.openPane("infrastructure");
    await settle(setup, 120);
    const frame = setup.captureCharFrame();
    if (process.env.DUMP_FRAME) console.log(frame);
    expect(frame).toContain("rikyu · public");
    expect(frame).toContain("canary-v1 · public");
    expect(frame).toContain("glm-5.3");
    expect(frame).toContain("kimi-k3");
    expect(frame).toContain("(no models listed)");
    expect(frame).not.toContain("unknown");
    setup.renderer.destroy();
  });

  it("one public source: its models are listed, not '(no models listed)'", async () => {
    const setup = await testRender(() => <App />, { width: 260, height: 60 });
    await setup.renderOnce();
    actions.closeAllOverlays();
    await settle(setup);
    setWorkspace("endpoints", [mapEndpoint(SINGLE_SOURCE_ENDPOINT as never)]);
    setWorkspace("models", SINGLE_SOURCE_MODELS.map((row) => mapModel(row as never)));
    actions.openPane("infrastructure");
    await settle(setup, 120);
    const frame = setup.captureCharFrame();
    expect(frame).toContain("rikyu · public (2)");
    expect(frame).toContain("kimi-k3");
    expect(frame).not.toContain("(no models listed)");
    expect(frame).not.toContain("unknown");
    setup.renderer.destroy();
  });
});
