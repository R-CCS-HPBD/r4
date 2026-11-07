/**
 * G8 (onboarding plan 2026-10-15 §5 B): the console never holds or sends a
 * source key. Platform sources are probed on the gateway's unauthenticated
 * readiness route (/ready), not {url}/models; a public source is probed only
 * where no key is needed, else reported "unknown". No request carries an
 * Authorization header, and no console source names R4_SOURCE_KEY.
 */
import { afterEach, describe, expect, test } from "bun:test";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { formatSourceHealthLines } from "../src/lib/health-report";
import { authenticatedCatalogRows } from "../src/lib/models-catalog";
import {
  NOT_PROBED_KEY,
  probeSource,
  sourceRowLabel,
  type SourceHealth,
  type SourceLike,
} from "../src/lib/source-health";
import type { SourceRow } from "../src/lib/sources";

interface Seen {
  url: string;
  headers: Headers;
}

const originalFetch = globalThis.fetch;

/** Replace fetch; `answer` maps a URL to a Response (or throws). */
function captureFetch(answer: (url: string) => Response): Seen[] {
  const seen: Seen[] = [];
  globalThis.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const headers = new Headers(input instanceof Request ? input.headers : undefined);
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
    seen.push({ url, headers });
    return answer(url);
  }) as typeof globalThis.fetch;
  return seen;
}

afterEach(() => {
  globalThis.fetch = originalFetch;
});

const ok = () => new Response("{}", { status: 200 });

const PLATFORM: SourceLike = { name: "rccs", kind: "platform", url: "https://gw.example/", api_key: true };
const KEYED_PUBLIC: SourceLike = { name: "rikyu", kind: "public", url: "https://rikyu.example/v1", api_key: true, provider: "openai" };

describe("probeSource — keyless probes only", () => {
  test("a platform source is probed on /ready, with no Authorization header", async () => {
    const seen = captureFetch(ok);
    const health = await probeSource(PLATFORM);
    expect(seen.map((s) => s.url)).toEqual(["https://gw.example/ready"]);
    expect(seen[0]!.headers.has("authorization")).toBe(false);
    expect(health.ok).toBe(true);
    expect(health.state).toBe("ok");
  });

  test("a not-ready gateway names the failing check (503 body)", async () => {
    captureFetch(
      () =>
        new Response('{"ready": false, "checks": {"gateway": "ok", "auth_sidecar": "ok", "umrs": "unreachable"}}', {
          status: 503,
        }),
    );
    const health = await probeSource(PLATFORM);
    expect(health.state).toBe("down");
    expect(health.detail).toMatch(/^not ready: umrs unreachable · \d+ms$/);
  });

  test("a direct-UMRS URL (/ready 404) falls back to /health", async () => {
    const seen = captureFetch((url) => (url.endsWith("/ready") ? new Response("", { status: 404 }) : ok()));
    const health = await probeSource(PLATFORM);
    expect(seen.map((s) => s.url)).toEqual(["https://gw.example/ready", "https://gw.example/health"]);
    expect(health.state).toBe("ok");
  });

  test("a public source holding a key is not probed: state unknown", async () => {
    const seen = captureFetch(ok);
    const health = await probeSource(KEYED_PUBLIC);
    expect(seen).toEqual([]);
    expect(health.ok).toBe(false);
    expect(health.state).toBe("unknown");
    expect(health.detail).toBe(NOT_PROBED_KEY);
  });

  test("key status unknown or bedrock auth → not probed", async () => {
    const seen = captureFetch(ok);
    const unknownKey = await probeSource({ name: "old", kind: "public", url: "https://x.example" });
    const bedrock = await probeSource({ name: "br", kind: "public", url: "https://br.example", api_key: false, provider: "bedrock" });
    expect(seen).toEqual([]);
    expect(unknownKey.state).toBe("unknown");
    expect(bedrock.state).toBe("unknown");
  });

  test("keyless public and ollama sources are probed without credentials", async () => {
    const seen = captureFetch(ok);
    await probeSource({ name: "vllm", kind: "public", url: "http://127.0.0.1:8000/v1", api_key: false });
    await probeSource({ name: "ollama", kind: "public", url: "http://127.0.0.1:11434", api_key: false, provider: "ollama" });
    expect(seen.map((s) => s.url)).toEqual(["http://127.0.0.1:8000/v1/models", "http://127.0.0.1:11434/api/tags"]);
    for (const s of seen) expect(s.headers.has("authorization")).toBe(false);
  });

  test("an Ollama source that holds a key (behind an auth proxy) is not probed: unknown, not down", async () => {
    const seen = captureFetch(() => new Response("unauthorized", { status: 401 }));
    const keyed = await probeSource({
      name: "lab-ollama",
      kind: "public",
      url: "https://ollama.lab.example",
      api_key: true,
      provider: "ollama",
    });
    expect(seen).toEqual([]);
    expect(keyed.state).toBe("unknown");
    expect(keyed.detail).toBe(NOT_PROBED_KEY);
  });

  test("an unreachable source is down, never thrown", async () => {
    captureFetch(() => {
      throw new Error("ECONNREFUSED");
    });
    const health = await probeSource(PLATFORM);
    expect(health.state).toBe("down");
    expect(health.detail).toMatch(/^unreachable · \d+ms$/);
  });
});

describe("models catalog — an authenticated fact the console does not fetch", () => {
  test("no catalog request: the rows wait for the r4 CLI (later wave)", async () => {
    const seen = captureFetch(ok);
    expect(await authenticatedCatalogRows()).toEqual([]);
    expect(seen).toEqual([]);
  });
});

describe("rendering the three states", () => {
  const row: SourceRow = { name: "rikyu", kind: "public", url: "https://rikyu.example/v1", active: true, has_api_key: true };
  const sample = (patch: Partial<SourceHealth>): SourceHealth => ({
    source: "rikyu",
    ok: false,
    latencyMs: 0,
    detail: NOT_PROBED_KEY,
    ...patch,
  });

  test("overlay row: ✓ ok, ✗ down, ? unknown, · not sampled; detail once", () => {
    expect(sourceRowLabel(row, sample({ ok: true, state: "ok", latencyMs: 12, detail: "ok · 12ms" }))).toBe(
      "● rikyu · public ✓ (ok · 12ms)",
    );
    expect(sourceRowLabel(row, sample({ state: "down", latencyMs: 9, detail: "http 503 · 9ms" }))).toBe(
      "● rikyu · public ✗ (http 503 · 9ms)",
    );
    expect(sourceRowLabel(row, sample({ state: "unknown" }))).toBe(`● rikyu · public ? (${NOT_PROBED_KEY})`);
    expect(sourceRowLabel({ ...row, active: false, kind: "platform", remote: "pushed (3m)" }, undefined)).toBe(
      "○ rikyu · platform · · pushed (3m)",
    );
  });

  test("an authenticated model count, once the r4 CLI supplies it, renders in the row", () => {
    expect(sourceRowLabel(row, sample({ ok: true, state: "ok", detail: "ok · 5ms", models: 7 }))).toBe(
      "● rikyu · public ✓ (ok · 5ms · 7 models)",
    );
  });

  test("/health: unknown is neither available nor down", () => {
    const rows: SourceRow[] = [row, { ...row, name: "local", active: false, has_api_key: false }];
    const lines = formatSourceHealthLines(rows, [
      sample({ state: "unknown" }),
      sample({ source: "local", ok: true, state: "ok", detail: "ok · 3ms" }),
    ]);
    expect(lines[0]).toBe("Sources (public): 1/2 available · 1 unknown (key stays with r4)");
    expect(lines).toContain(`  ? rikyu (active) — ${NOT_PROBED_KEY}`);
    expect(lines).toContain("  ● local — ok · 3ms");
  });
});

describe("no console source sends a source key", () => {
  function sourceFiles(dir: string): string[] {
    return readdirSync(dir).flatMap((name) => {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) return sourceFiles(path);
      return /\.(ts|tsx)$/.test(name) ? [path] : [];
    });
  }

  /** Any way of putting a credential on a request: an object-literal header
   *  in any case, Headers.set/append ("authorization", …), a property write,
   *  an x-api-key header, or a string that starts "Bearer ". */
  const SENDS_A_KEY = [
    /R4_SOURCE_KEY/,
    /["']?authorization["']?\s*[:,=]/i,
    /["']?x-api-key["']?\s*[:,=]/i,
    /["'`]Bearer\s/,
  ];

  test("no R4_SOURCE_KEY literal and no auth header or bearer string in src/", () => {
    const offenders = sourceFiles(join(import.meta.dir, "..", "src")).filter((path) => {
      const text = readFileSync(path, "utf8");
      return SENDS_A_KEY.some((pattern) => pattern.test(text));
    });
    expect(offenders).toEqual([]);
  });

  test("the scan catches the setter, lowercase and bearer forms (guard self-test)", () => {
    const leaks = [
      'h.set("Authorization", `Bearer ${key}`)',
      "fetch(url, { headers: { authorization: token } })",
      'h.append("authorization", "Bearer " + key)',
      "headers.Authorization = token",
      'fetch(url, { headers: { "x-api-key": key } })',
      "const auth = `Bearer ${key}`;",
    ];
    for (const leak of leaks) expect(SENDS_A_KEY.some((pattern) => pattern.test(leak))).toBe(true);
    expect(SENDS_A_KEY.some((pattern) => pattern.test("// no authorization header is ever sent"))).toBe(false);
  });
});
