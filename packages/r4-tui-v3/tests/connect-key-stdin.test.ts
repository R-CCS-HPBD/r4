/**
 * S4 (onboarding plan 2026-10-15 §1): /connect hands a provider key to
 * `r4 source add` on stdin (`--api-key-stdin`), never in argv, and no text
 * the console shows (result, error, timeout, missing r4) carries the key.
 *
 * A fake interpreter stands in for r4 (tests/helpers/fake-r4.ts).
 */
import { afterEach, describe, expect, test } from "bun:test";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  addPublicSource,
  loginWithJwt,
  parseSourceAddArgs,
  publicSourceArgs,
  STDIN_FLAG_MISSING,
} from "../src/lib/connect";
import { PROVIDER_KEY, makeFakeR4, restoreR4, useFakeR4 } from "./helpers/fake-r4";

const JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzNCJ9.c2lnbmF0dXJlLVM0";

afterEach(restoreR4);

const SOURCE = { name: "rikyu", url: "https://rikyu.example/v1", apiKey: PROVIDER_KEY };

describe("addPublicSource — the key rides stdin", () => {
  test("argv carries --api-key-stdin and never the key; stdin carries the key", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const res = await addPublicSource(SOURCE);
    expect(res.ok).toBe(true);
    const argv = fake.argv();
    expect(argv.slice(0, 7)).toEqual([
      "source", "add", "rikyu", "--url", "https://rikyu.example/v1", "--kind", "public",
    ]);
    expect(argv).toContain("--api-key-stdin");
    expect(argv.join(" ")).not.toContain(PROVIDER_KEY);
    expect(fake.stdin()).toBe(PROVIDER_KEY);
  });

  test("a key echoed back by r4 never reaches the result text", async () => {
    const fake = makeFakeR4("echo");
    useFakeR4(fake.python);
    const res = await addPublicSource(SOURCE);
    expect(res.ok).toBe(false);
    expect(res.output).not.toContain(PROVIDER_KEY);
    expect(res.output).toContain("•••");
  });

  test("a timeout is a failure, not a success, and names no key", async () => {
    const fake = makeFakeR4("sleep");
    useFakeR4(fake.python);
    const started = Date.now();
    const res = await addPublicSource(SOURCE, { timeoutMs: 300 });
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/timed out/);
    expect(res.output).not.toContain(PROVIDER_KEY);
    expect(Date.now() - started).toBeLessThan(4_000);
  });

  test("r4 missing → a console-authored failure, no throw, no key", async () => {
    useFakeR4(join(tmpdir(), "r4-missing-interpreter", "python"));
    const res = await addPublicSource(SOURCE);
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/r4 CLI not found/);
    expect(res.output).not.toContain(PROVIDER_KEY);
  });

  test("without a key, stdin stays empty and no stdin flag is passed", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const res = await addPublicSource({ name: "local", url: "http://127.0.0.1:8000/v1" });
    expect(res.ok).toBe(true);
    expect(fake.argv()).not.toContain("--api-key-stdin");
    expect(fake.stdin()).toBe("");
  });
});

describe("loginWithJwt — the token rides stdin and never comes back", () => {
  test("argv is `auth login --api-key-stdin`; the token is on stdin only", async () => {
    const fake = makeFakeR4("ok");
    useFakeR4(fake.python);
    const res = await loginWithJwt(JWT);
    expect(res.ok).toBe(true);
    expect(fake.argv()).toEqual(["auth", "login", "--api-key-stdin"]);
    expect(fake.stdin()).toBe(JWT);
  });

  test("r4's echo of the token is scrubbed from the result", async () => {
    const fake = makeFakeR4("echo");
    useFakeR4(fake.python);
    const res = await loginWithJwt(JWT);
    expect(res.ok).toBe(false);
    expect(res.output).not.toContain(JWT);
  });

  test("a timeout is a failure and names no token", async () => {
    const fake = makeFakeR4("sleep");
    useFakeR4(fake.python);
    const res = await loginWithJwt(JWT, { timeoutMs: 300 });
    expect(res.ok).toBe(false);
    expect(res.output).toMatch(/timed out/);
    expect(res.output).not.toContain(JWT);
  });
});

describe("an r4 without --api-key-stdin fails closed", () => {
  test("login and source add say to upgrade; neither retries with the key in argv", async () => {
    const fake = makeFakeR4("old");
    useFakeR4(fake.python);
    const login = await loginWithJwt(JWT);
    expect(login).toEqual({ ok: false, output: STDIN_FLAG_MISSING });
    expect(fake.argv().join(" ")).not.toContain(JWT);
    const added = await addPublicSource(SOURCE);
    expect(added).toEqual({ ok: false, output: STDIN_FLAG_MISSING });
    expect(fake.argv().join(" ")).not.toContain(PROVIDER_KEY);
  });
});

describe("publicSourceArgs (pure)", () => {
  test("a key turns into --api-key-stdin; an env var passes only its name", () => {
    expect(publicSourceArgs(SOURCE)).toEqual([
      "source", "add", "rikyu", "--url", "https://rikyu.example/v1", "--kind", "public", "--api-key-stdin",
    ]);
    const viaEnv = publicSourceArgs({ name: "or", url: "https://or.example", apiKeyEnv: "OPENROUTER_KEY" });
    expect(viaEnv).toContain("--api-key-env");
    expect(viaEnv).toContain("OPENROUTER_KEY");
    expect(viaEnv).not.toContain("--api-key-stdin");
  });
});

describe("parseSourceAddArgs (pure)", () => {
  test("parses name, url, key, provider, quoted description, insecure flag", () => {
    const parsed = parseSourceAddArgs([
      "rikyu", "--url", "https://rikyu.example/v1", `--api-key=${PROVIDER_KEY}`,
      "--provider", "openai", "--description", "Rikyu institutional", "--allow-insecure-http",
    ]);
    expect(parsed).toEqual({
      ok: true,
      opts: {
        name: "rikyu",
        url: "https://rikyu.example/v1",
        apiKey: PROVIDER_KEY,
        provider: "openai",
        description: "Rikyu institutional",
        allowInsecureHttp: true,
      },
    });
  });

  test("errors name the flag, never a value", () => {
    const cases: string[][] = [
      ["rikyu", "--api-key", PROVIDER_KEY], // no --url
      ["--api-key", PROVIDER_KEY, "--url", "https://x"], // no name
      ["rikyu", "--url", "https://x", PROVIDER_KEY], // stray positional (forgot the flag)
      ["rikyu", "--url", "https://x", `--apikey=${PROVIDER_KEY}`], // misspelt flag
      ["rikyu", "--url", "https://x", "--api-key"], // flag without a value
      ["rikyu", "--url", "--api-key", PROVIDER_KEY], // flag value is another flag
    ];
    for (const tokens of cases) {
      const parsed = parseSourceAddArgs(tokens);
      expect(parsed.ok).toBe(false);
      if (!parsed.ok) expect(parsed.error).not.toContain(PROVIDER_KEY);
    }
  });
});
