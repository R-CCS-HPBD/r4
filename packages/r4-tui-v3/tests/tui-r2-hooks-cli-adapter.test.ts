/**
 * TUI audit r2 (2026-10-17), hooks lane — the r4 CLI adapter.
 *  #45/#51  a killed (timed-out) CLI child is a failure, never exit 0
 *  #52      gateway down: the local skill probe must not mask the outage
 *  #45/#56  a failed probe / a failed source keeps its previous rows and is
 *           reported as a problem (degraded), never wiped to []
 *  #53      research sessions are fetched without local import files, and a
 *           client `{error}` is "unavailable", not "no sessions"
 *  #54      project-scoped subprocesses carry the active tab's project; the
 *           global inventory probes do not (review: a tab switch swapped the
 *           job list and toasted every job as "created")
 * The `r4` CLI and the knowledge script run against a fake interpreter
 * (R4_TUI_PYTHON) whose answers come from FAKE_* env vars.
 */
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "bun:test";
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createRoot } from "solid-js";
import { R4CliDataSource, _setKnowledgeTimeoutForTests, cliExitCode } from "../src/adapters/r4-cli";
import { createBackendPoller, stopBackendPoller } from "../src/hooks/useBackend";
import { setDemoMode } from "../src/lib/console-mode";
import { resetResolvedPythonCache } from "../src/lib/resolve-python";
import { resetWorkspace, setWorkspace, workspace } from "../src/stores";

const FAKE_KEYS = ["FAKE_ENDPOINTS", "FAKE_MODELS", "FAKE_JOBS", "FAKE_SKILLS", "FAKE_KNOWLEDGE"];
const saved: Record<string, string | undefined> = {};
let dir = "";

const SCRIPT = `#!/bin/sh
shift 2
case "$1 $2" in
  "endpoint list") eval "$FAKE_ENDPOINTS" ;;
  "model list") eval "$FAKE_MODELS" ;;
  "job list") eval "$FAKE_JOBS" ;;
  "skill list") eval "$FAKE_SKILLS" ;;
  *) cat > /dev/null; eval "$FAKE_KNOWLEDGE" ;;
esac
`;

beforeAll(() => {
  for (const key of ["R4_TUI_PYTHON", "R4_PROJECT_ID", ...FAKE_KEYS]) saved[key] = process.env[key];
  dir = mkdtempSync(join(tmpdir(), "tui-r2-cli-"));
  const fake = join(dir, "fake-python");
  writeFileSync(fake, SCRIPT);
  chmodSync(fake, 0o755);
  process.env["R4_TUI_PYTHON"] = fake;
  delete process.env["R4_PROJECT_ID"];
  resetResolvedPythonCache();
});

afterAll(() => {
  for (const [key, value] of Object.entries(saved)) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  resetResolvedPythonCache();
  rmSync(dir, { recursive: true, force: true });
});

const ok = (json: unknown) => `echo '${JSON.stringify(json)}'`;
const ep = (id: string, source: string) => ({ instance_id: id, model: `m-${id}`, status: "ready", _source: source });

beforeEach(() => {
  resetWorkspace();
  process.env["FAKE_ENDPOINTS"] = ok({ endpoints: [ep("ep1", "plat")] });
  process.env["FAKE_MODELS"] = ok({ models: [{ id: "m1", name: "m1" }] });
  process.env["FAKE_JOBS"] = ok({ jobs: [] });
  process.env["FAKE_SKILLS"] = ok([]);
  process.env["FAKE_KNOWLEDGE"] = ok({ sessions: [] });
});

describe("killed CLI child", () => {
  it("a signal-killed child maps to a failure (124), not exit 0", async () => {
    const proc = Bun.spawn(["sleep", "5"], { signal: AbortSignal.timeout(100) });
    await proc.exited;
    expect(proc.exitCode).toBeNull();
    expect(cliExitCode(proc)).toBe(124);
    expect(cliExitCode({ exitCode: 0, signalCode: null })).toBe(0);
  });

  it("runAction reports a timed-out r4 child as a failure, never the canned success", async () => {
    process.env["FAKE_KNOWLEDGE"] = "exec sleep 5";
    const source = new R4CliDataSource() as unknown as {
      runAction(args: string[], okMessage: string, timeoutMs: number): Promise<{ ok: boolean; message: string }>;
    };
    const started = Date.now();
    const result = await source.runAction(["endpoint", "up", "--model", "m"], "Requested endpoint for m", 300);
    expect(Date.now() - started).toBeLessThan(3000);
    expect(result.ok).toBe(false);
    expect(result.message).toContain("timed out");
    expect(result.message).not.toContain("Requested endpoint");
  });

  it("provisionEndpoint gives the submit the CLI's own budget plus a margin", async () => {
    const saved = process.env["R4_PROVISION_SUBMIT_TIMEOUT_SEC"];
    process.env["R4_PROVISION_SUBMIT_TIMEOUT_SEC"] = "120";
    const source = new R4CliDataSource();
    let budget = 0;
    (source as unknown as { runAction: (a: string[], m: string, t: number) => Promise<unknown> }).runAction = async (
      _a,
      _m,
      t,
    ) => {
      budget = t;
      return { ok: true, message: "" };
    };
    await source.provisionEndpoint("m");
    if (saved === undefined) delete process.env["R4_PROVISION_SUBMIT_TIMEOUT_SEC"];
    else process.env["R4_PROVISION_SUBMIT_TIMEOUT_SEC"] = saved;
    expect(budget).toBe(150_000);
  });

  it("a timed-out knowledge child answers {error: timed out}", async () => {
    process.env["FAKE_KNOWLEDGE"] = "exec sleep 5";
    _setKnowledgeTimeoutForTests(300);
    try {
      const payload = await new R4CliDataSource().catalogStats();
      expect(String(payload["error"])).toContain("timed out");
    } finally {
      _setKnowledgeTimeoutForTests();
    }
  });
});

describe("pollBackend", () => {
  it("throws when every gateway probe fails even though the local skill probe answers", async () => {
    process.env["FAKE_ENDPOINTS"] = "echo 'connection refused' >&2; exit 1";
    process.env["FAKE_MODELS"] = "exit 1";
    process.env["FAKE_JOBS"] = "exit 1";
    await expect(new R4CliDataSource().pollBackend()).rejects.toThrow(/unreachable/);
  });

  // slash-a#4 (2026-10-21): the note showed the LAST stderr line — the tail
  // of r4's multi-line hint — and called missing credentials "unreachable".
  it("names r4's Error line, not the hint's tail, and says 'not signed in' for missing credentials", async () => {
    process.env["FAKE_ENDPOINTS"] =
      "printf '%s\\n' 'Error (endpoint list): No credentials configured.' '  hint: Set up credentials first:' " +
      "'  3. Verify:' '       r4 health' >&2; exit 1";
    process.env["FAKE_MODELS"] = "exit 1";
    process.env["FAKE_JOBS"] = "exit 1";
    const error = await new R4CliDataSource().pollBackend().then(
      () => null,
      (err: Error) => err.message,
    );
    expect(error).toContain("endpoints: No credentials configured.");
    expect(error).toContain("not signed in");
    expect(error).toContain("/connect");
    expect(error).not.toContain("r4 health");
    expect(error).not.toContain("unreachable");
  });

  it("keeps the previous rows of a failed probe and names the problem", async () => {
    const source = new R4CliDataSource();
    await source.pollBackend();
    process.env["FAKE_MODELS"] = "echo 'gateway timeout' >&2; exit 1";
    const snap = await source.pollBackend();
    expect(snap.models.map((m) => m.id)).toEqual(["m1"]);
    expect(snap.endpoints.map((e) => e.id)).toEqual(["ep1"]);
    expect(snap.problems?.join(" ")).toContain("models: gateway timeout");
  });

  it("a source the multi-source listing reports in `errors` keeps its rows and surfaces the hint", async () => {
    const source = new R4CliDataSource();
    await source.pollBackend();
    process.env["FAKE_ENDPOINTS"] = ok({
      endpoints: [{ instance_id: "public", status: "configured", _source: "pub" }],
      errors: [{ source: "plat", error: "401 Unauthorized", hint: "run r4 login" }],
    });
    const snap = await source.pollBackend();
    expect(snap.endpoints.map((e) => e.id).sort()).toEqual(["ep1", "public"]);
    expect(snap.problems?.join(" ")).toContain("run r4 login");
  });

  it("a clean poll reports no problems", async () => {
    const snap = await new R4CliDataSource().pollBackend();
    expect(snap.problems ?? []).toEqual([]);
  });
});

describe("research sessions + tab project", () => {
  const projectId = "tui-r2-no-local-files-0001";

  it("fetches sessions for a project without local import files", async () => {
    process.env["FAKE_KNOWLEDGE"] = ok({ sessions: [{ session_id: "s1", title: "t", status: "active" }] });
    const result = await new R4CliDataSource().pollProjectKnowledge(projectId);
    expect(result.sessions?.length).toBe(1);
  });

  it("an {error} answer is unavailable (null), not an empty list", async () => {
    process.env["FAKE_KNOWLEDGE"] = ok({ error: "502 Bad Gateway" });
    const result = await new R4CliDataSource().pollProjectKnowledge(projectId);
    expect(result.sessions).toBeNull();
  });

  it("knowledge subprocesses carry the active tab's project", async () => {
    process.env["FAKE_KNOWLEDGE"] = `echo "{\\"project\\": \\"$R4_PROJECT_ID\\"}"`;
    setWorkspace("project", { id: "PRO_TAB", name: "PRO_TAB", description: "", status: "active", goalIds: [] });
    const payload = await new R4CliDataSource().catalogStats();
    expect(payload["project"]).toBe("PRO_TAB");
  });
});

// One job per project: the id names the R4_PROJECT_ID the probe ran with.
const JOBS_PER_PROJECT = `echo '{"jobs": [{"id": "job-'"$R4_PROJECT_ID"'", "status": "running"}]}'`;

describe("global inventory vs the tab project", () => {
  const tabProject = (id: string) =>
    setWorkspace("project", { id, name: id, description: "", status: "active", goalIds: [] });

  afterEach(() => stopBackendPoller());

  it("the project-scoped `r4 catalog list` call does carry it", async () => {
    process.env["FAKE_KNOWLEDGE"] = `echo '{"project": "'"$R4_PROJECT_ID"'"}'`;
    tabProject("PRO_CAT");
    const payload = await new R4CliDataSource().scientificCatalogs();
    expect(payload["project"]).toBe("PRO_CAT");
  });

  it("the inventory probes do not carry the tab's project", async () => {
    process.env["FAKE_JOBS"] = JOBS_PER_PROJECT;
    tabProject("PRO_A");
    const snap = await new R4CliDataSource().pollBackend();
    expect(snap.jobs.map((j) => j.id)).toEqual(["job-"]);
  });

  it("switching tab projects between polls emits no job-created events", async () => {
    process.env["FAKE_JOBS"] = JOBS_PER_PROJECT;
    setDemoMode(false);
    tabProject("PRO_A");
    const polls: number[] = [];
    const source = new R4CliDataSource();
    const poll = source.pollBackend.bind(source);
    source.pollBackend = async () => {
      const snap = await poll();
      polls.push(polls.length);
      return snap;
    };
    createRoot(() => createBackendPoller(source, 20));
    for (let i = 0; i < 400 && polls.length < 1; i++) await new Promise((r) => setTimeout(r, 10));
    tabProject("PRO_B");
    for (let i = 0; i < 400 && polls.length < 3; i++) await new Promise((r) => setTimeout(r, 10));
    expect(polls.length).toBeGreaterThanOrEqual(3);
    expect(workspace.events.filter((e) => e.action === "job-created")).toEqual([]);
    expect(workspace.notifications.filter((n) => n.title === "Job update")).toEqual([]);
  });
});
