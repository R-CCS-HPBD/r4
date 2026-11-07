/**
 * /connect wizard (2026-10-19): the menu's Login / Add-public-endpoint rows
 * open a question-and-answer form that builds the SAME slash line
 * (`/login <token>` / `/source add … --api-key <k>`) and dispatches it
 * through the masked path — answers never need manual slash skill, and a
 * secret still reaches r4 only on stdin, never argv or the transcript.
 *
 * 2026-10-20 field report (connect from another PC failed, CLI worked):
 * a silent 512-char draft cap truncated pasted JWTs past it (login stored a
 * broken credential yet reported success), and a credentialed http:// URL
 * had no path to r4's G-4 opt-in (`--allow-insecure-http`). Pins below:
 * secrets carry a ≥4096 cap, and the G-4 question appears exactly when the
 * answers need it, adding the flag to the built line.
 *
 * 2026-10-21 follow-up: successes were silent — login printed only
 * "reconnecting…" and a source add printed only "registered", so a wedged
 * reconnect or a rejected key both read as "nothing later". Pins: the login
 * receipt ("bridge reconnected — signed in …") and the add probe receipt
 * (named failure / served count / honest pending).
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";

import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { stopBackendPoller } from "../src/hooks/useBackend";
import { stopKnowledgePoller } from "../src/hooks/useKnowledge";
import { buildWizardLine, connectQuestions } from "../src/lib/connect-wizard";
import { parseSourceAddArgs, splitSlashWords } from "../src/lib/source-add-args";
import { setDemoMode } from "../src/lib/console-mode";
import { actions, resetWorkspace, workspace } from "../src/stores";
import { agentActions } from "../src/stores/agent-runtime";
import { PROVIDER_KEY, makeFakeR4, restoreR4, useFakeR4 } from "./helpers/fake-r4";

// Synthetic JWT test double (sub "wizard", sig "sig-wizard") — assembled,
// never a one-line literal: ci-lint-local §0's JWT regex has no fixture
// allowlist and would flag it on the push range (2026-10-19).
const JWT = ["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiJ3aXphcmQifQ", "c2lnLXdpemFyZA"].join(".");

/** Bridge stub: login reconnects it; the source add probes the new source
 * through list_models (2026-10-21 receipts), other methods throw. */
function stubBridge(listModels: Record<string, unknown> = { models: [], problems: [] }) {
  return {
    request: async (method: string) => {
      if (method === "list_models") return listModels;
      throw new Error(`unexpected bridge call: ${method}`);
    },
    reconnect: async () => {},
  };
}

function transcript(): string {
  return workspace.chatMessages.map((m) => m.content).join("\n");
}

describe("connect wizard — questions and line building", () => {
  test("login asks one required secret question; source asks five in order", () => {
    const login = connectQuestions("login");
    expect(login.length).toBe(1);
    expect(login[0]!.secret).toBe(true);
    expect(login[0]!.required).toBe(true);
    const source = connectQuestions("source");
    expect(source.map((q) => q.key)).toEqual(["name", "url", "apiKey", "provider", "description"]);
    expect(source.filter((q) => q.required).map((q) => q.key)).toEqual(["name", "url"]);
    expect(source.find((q) => q.key === "apiKey")!.secret).toBe(true);
  });

  test("validators reject a spaced name and a non-http url", () => {
    const source = connectQuestions("source");
    const name = source.find((q) => q.key === "name")!;
    const url = source.find((q) => q.key === "url")!;
    expect(name.validate!("two words")).toContain("no spaces");
    expect(url.validate!("rikyu.example/v1")).toContain("http");
    expect(url.validate!("https://rikyu.example/v1")).toBeNull();
  });

  test("secrets allow real token/key lengths (no 512 silent-cap comeback)", () => {
    // A 512-char cap once truncated pasted JWTs — HS256 onboarding ≈470-650,
    // RS256 ≈770-900 chars. The cap lives on the question and stays well past.
    expect(connectQuestions("login")[0]!.maxDraft).toBeGreaterThanOrEqual(4096);
    const apiKey = connectQuestions("source").find((q) => q.key === "apiKey")!;
    expect(apiKey.maxDraft).toBeGreaterThanOrEqual(4096);
  });

  test("a long login token survives the whole line build verbatim", () => {
    // Built as a repeat so ci-lint §0's JWT regex cannot trip on it.
    const token = `h.${"p".repeat(900)}.sig`;
    const line = buildWizardLine("login", [token]);
    expect(line).toBe(`/login ${token}`);
    expect(splitSlashWords(line).words.map((w) => w.value)).toEqual(["/login", token]);
  });

  test("the G-4 opt-in question appears exactly when a key would travel plain http", () => {
    const keys = (qs: { key: string }[]) => qs.map((q) => q.key);
    const httpKey = ["rikyu", "http://10.0.0.1:8000/v1", "k"];
    expect(keys(connectQuestions("source", httpKey))).toContain("allowInsecureHttp");
    // https, loopback http, and keyless http add no question.
    expect(keys(connectQuestions("source", ["rikyu", "https://rikyu.example/v1", "k"]))).not.toContain(
      "allowInsecureHttp",
    );
    expect(keys(connectQuestions("source", ["ollama", "http://localhost:11434", "k"]))).not.toContain(
      "allowInsecureHttp",
    );
    expect(keys(connectQuestions("source", ["rikyu", "http://10.0.0.1:8000/v1", ""]))).not.toContain(
      "allowInsecureHttp",
    );
    const g4 = connectQuestions("source", httpKey).find((q) => q.key === "allowInsecureHttp")!;
    expect(g4.required).toBe(false);
    expect(g4.validate!("yes")).toBeNull();
    expect(g4.validate!("maybe")).toBeTruthy();
  });

  test("the login line round-trips through the slash word splitter", () => {
    const line = buildWizardLine("login", [JWT]);
    expect(line).toBe(`/login ${JWT}`);
    expect(splitSlashWords(line).words.map((w) => w.value)).toEqual(["/login", JWT]);
  });

  test("the source line round-trips through the one grammar, quotes intact", () => {
    const answers = [
      "rikyu",
      "https://rikyu.example/v1",
      PROVIDER_KEY,
      "openai",
      'Rikyu "institutional" cluster',
    ];
    const line = buildWizardLine("source", answers);
    const { words } = splitSlashWords(line);
    expect(words.slice(0, 2).map((w) => w.value)).toEqual(["/source", "add"]);
    const parsed = parseSourceAddArgs(words.slice(2).map((w) => w.value));
    if (!parsed.ok) throw new Error(parsed.error);
    expect(parsed.opts).toEqual({
      name: "rikyu",
      url: "https://rikyu.example/v1",
      apiKey: PROVIDER_KEY,
      provider: "openai",
      description: 'Rikyu "institutional" cluster',
    });
  });

  test("skipped optionals leave no flags behind", () => {
    const line = buildWizardLine("source", ["rikyu", "https://rikyu.example/v1", "", "", ""]);
    expect(line).toBe("/source add rikyu --url https://rikyu.example/v1");
  });

  test("the G-4 yes adds --allow-insecure-http; every other answer does not", () => {
    const base = ["rikyu", "http://10.0.0.1:8000/v1", "k", "", ""];
    expect(buildWizardLine("source", [...base, "yes"])).toContain("--allow-insecure-http");
    expect(buildWizardLine("source", [...base, "y"])).toContain("--allow-insecure-http");
    for (const no of ["", "no", "n"]) {
      expect(buildWizardLine("source", [...base, no])).not.toContain("--allow-insecure-http");
    }
  });

  test("the opted-in line round-trips through the one grammar, flag intact", () => {
    const line = buildWizardLine("source", ["rikyu", "http://10.0.0.1:8000/v1", "k", "", "", "yes"]);
    const { words } = splitSlashWords(line);
    const parsed = parseSourceAddArgs(words.slice(2).map((w) => w.value));
    if (!parsed.ok) throw new Error(parsed.error);
    expect(parsed.opts.allowInsecureHttp).toBe(true);
    expect(parsed.opts.apiKey).toBe("k");
  });
});

describe("connect wizard — dispatched lines keep secrets off the transcript", () => {
  beforeEach(() => {
    stopBackendPoller();
    stopKnowledgePoller();
    setDemoMode(false);
    agentActions.resetAgentRuntime();
    resetWorkspace();
    actions.closeAllOverlays();
    setCompanionBridgeForTests(stubBridge() as never);
  });

  afterEach(() => {
    setCompanionBridgeForTests(null);
    restoreR4();
  });

  test("the wizard's login answer authenticates via stdin; echo stays masked", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const result = await actions.dispatchSlashFromChat(buildWizardLine("login", [JWT]));
    expect(result.kind).toBe("handled");
    // Call 1 is the login proper; call 2 is the sign-in receipt's auth probe.
    expect(fake.calls()[0]!.argv).toEqual(["auth", "login", "--api-key-stdin"]);
    expect(fake.calls()[0]!.stdin).toBe(JWT);
    expect(fake.calls()[1]!.argv).toEqual(["auth", "status"]);
    expect(transcript()).toContain("/login •••");
    expect(transcript()).not.toContain(JWT);
  });

  test("a successful login prints a sign-in receipt (no more silent 'nothing later')", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    await actions.dispatchSlashFromChat(buildWizardLine("login", [JWT]));
    // Field report 2026-10-21: success printed only "login ok —
    // reconnecting…" and nothing after; the receipt names the state instead.
    expect(transcript()).toContain("bridge reconnected — signed in");
  });

  test("a >512-char login token reaches r4 stdin byte-exact (no silent cap)", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    // 900-char payload segment: twice the old silent cap; repeat-built so
    // ci-lint §0's JWT regex never matches the fixture.
    const token = `h.${"p".repeat(900)}.sig`;
    const result = await actions.dispatchSlashFromChat(buildWizardLine("login", [token]));
    expect(result.kind).toBe("handled");
    expect(fake.calls()[0]!.argv).toEqual(["auth", "login", "--api-key-stdin"]);
    expect(fake.calls()[0]!.stdin).toBe(token);
    expect(transcript()).not.toContain(token);
  });

  test("the wizard's source answers register with the key on stdin, echo masked", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const answers = ["rikyu", "https://rikyu.example/v1", PROVIDER_KEY, "", "Rikyu institutional"];
    const result = await actions.dispatchSlashFromChat(buildWizardLine("source", answers));
    expect(result.kind).toBe("handled");
    const argv = fake.argv();
    expect(argv.slice(0, 3)).toEqual(["source", "add", "rikyu"]);
    expect(argv).toContain("--api-key-stdin");
    expect(argv.join(" ")).not.toContain(PROVIDER_KEY);
    expect(fake.stdin()).toBe(PROVIDER_KEY);
    expect(transcript()).toContain("--api-key •••");
    expect(transcript()).not.toContain(PROVIDER_KEY);
    // Default stub probe: nothing served yet, nothing failed — honest pending.
    expect(transcript()).toContain("live probe pending");
  });

  test("a registered source whose probe fails gets its failure named in the receipt", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    setCompanionBridgeForTests(
      stubBridge({ models: [], problems: [{ source: "rikyu", error: "API key rejected" }] }) as never,
    );
    const answers = ["rikyu", "https://rikyu.example/v1", PROVIDER_KEY, "", ""];
    const result = await actions.dispatchSlashFromChat(buildWizardLine("source", answers));
    expect(result.kind).toBe("handled");
    expect(transcript()).toContain("probe: API key rejected");
  });

  test("a registered source whose probe answers prints its served count", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    setCompanionBridgeForTests(
      stubBridge({ models: [{ name: "m1@rikyu" }, { name: "m2@rikyu" }], problems: [] }) as never,
    );
    const answers = ["rikyu", "https://rikyu.example/v1", PROVIDER_KEY, "", ""];
    const result = await actions.dispatchSlashFromChat(buildWizardLine("source", answers));
    expect(result.kind).toBe("handled");
    expect(transcript()).toContain("reachable — 2 models served");
  });
});

describe("connect wizard — store wiring", () => {
  beforeEach(() => {
    resetWorkspace();
    actions.closeAllOverlays();
  });

  test("openConnectWizard replaces the /connect menu overlay with the form", () => {
    actions.openListOverlay("connect");
    actions.openConnectWizard("login");
    expect(workspace.listOverlay).toBeNull();
    expect(workspace.connectWizard).toEqual({ flow: "login" });
  });

  test("the global Esc sweep closes the wizard", () => {
    actions.openConnectWizard("source");
    actions.closeAllOverlays();
    expect(workspace.connectWizard).toBeNull();
  });
});
