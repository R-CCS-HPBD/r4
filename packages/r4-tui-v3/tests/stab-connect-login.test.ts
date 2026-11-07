/**
 * Stabilization lane `connect` (2026-10-21) — /login pins.
 *
 *  slash-b#0  `/login help` (or --help) piped "help" to `r4 auth login`, which
 *             stored it over the real key; the TUI said "login ok".
 *  connect#6  a token the gateway rejects said "login ok", then "auth status
 *             unclear; try /health" — r4 stores without asking the gateway,
 *             and the TUI dropped what `r4 auth status` said.
 */
import { afterEach, beforeEach, describe, expect, test } from "bun:test";
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { setCompanionBridgeForTests } from "../src/bridge/jsonl-client";
import { loginReceipt, loginTokenOf } from "../src/lib/connect";
import { setDemoMode } from "../src/lib/console-mode";
import { resetResolvedPythonCache } from "../src/lib/resolve-python";
import { actions, resetWorkspace, workspace } from "../src/stores";

const savedPython = process.env.R4_TUI_PYTHON;
let dir = "";

/** A fake r4 answering `auth login` / `auth status`; records each call. */
function useFakeR4(status: string): void {
  dir = mkdtempSync(join(tmpdir(), "stab-login-"));
  const python = join(dir, "python");
  writeFileSync(
    python,
    [
      "#!/usr/bin/env bash",
      "shift 2",
      `echo "$1 $2" >> "${dir}/calls"`,
      `cat > "${dir}/stdin-$2"`,
      'case "$1 $2" in',
      `  "auth login") echo "✓ Credentials configured for profile: default" ;;`,
      `  "auth status") ${status} ;;`,
      "esac",
      "",
    ].join("\n"),
  );
  chmodSync(python, 0o755);
  process.env.R4_TUI_PYTHON = python;
  resetResolvedPythonCache();
}

function calls(): string[] {
  const file = join(dir, "calls");
  return existsSync(file) ? readFileSync(file, "utf8").split("\n").filter(Boolean) : [];
}

function transcript(): string {
  return workspace.chatMessages.map((m) => m.content).join("\n");
}

const REJECTED =
  `printf '%s\\n' "✗ Profile 'default' has credentials, but Gateway rejected them." ` +
  `"  Gateway: http://127.0.0.1:9" "  Error: HTTP 401"; exit 1`;

beforeEach(() => {
  setDemoMode(false);
  resetWorkspace();
  setCompanionBridgeForTests({ request: async () => ({}), reconnect: async () => {} } as never);
});

afterEach(() => {
  setCompanionBridgeForTests(null);
  if (savedPython === undefined) delete process.env.R4_TUI_PYTHON;
  else process.env.R4_TUI_PYTHON = savedPython;
  resetResolvedPythonCache();
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = "";
});

describe("/login refuses what cannot be a token (slash-b#0)", () => {
  for (const args of ["help", "--help", "-h", "HELP", "?", "two words", "'open quote"]) {
    test(`/login ${args} → usage; r4 never runs`, async () => {
      useFakeR4("exit 0");
      const result = await actions.dispatchSlashFromChat(`/login ${args}`);
      expect(result.kind).toBe("error");
      expect(result.kind === "error" ? result.message : "").toContain("usage: /login <JWT-token>");
      expect(calls()).toEqual([]);
      expect(transcript()).not.toContain("login ok");
    });
  }

  test("a pasted `Bearer <token>` sends just the token", async () => {
    useFakeR4("exit 0");
    await actions.dispatchSlashFromChat("/login Bearer tok-123");
    expect(calls()[0]).toBe("auth login");
    expect(readFileSync(join(dir, "stdin-login"), "utf8")).toBe("tok-123");
  });

  test("loginTokenOf keeps key-only gateway API keys and quoted tokens", () => {
    expect(loginTokenOf("sk-gateway-opaque-0123")).toBe("sk-gateway-opaque-0123");
    expect(loginTokenOf(`'a"b'`)).toBe('a"b');
    expect(loginTokenOf("")).toBeNull();
    expect(loginTokenOf("--api-key x")).toBeNull();
  });
});

describe("/login reports a token the gateway rejects (connect#6)", () => {
  test("rejected → an error naming the rejection; never 'login ok', never 'try /health'", async () => {
    useFakeR4(REJECTED);
    const result = await actions.dispatchSlashFromChat("/login garbage-token-xyz");
    expect(calls()).toEqual(["auth login", "auth status"]);
    expect(result.kind).toBe("error");
    const message = result.kind === "error" ? result.message : "";
    expect(message).toContain("token rejected by the gateway");
    expect(message).toContain("/connect");
    expect(`${transcript()}\n${message}`).not.toContain("login ok");
    expect(`${transcript()}\n${message}`).not.toContain("try /health");
  });

  test("accepted → the signed-in receipt", async () => {
    useFakeR4(`printf '%s\\n' "✓ ok" "    Username: alice" "    Expires in: 1h"`);
    const result = await actions.dispatchSlashFromChat("/login good-token");
    expect(result.kind).toBe("handled");
    expect(transcript()).toContain("bridge reconnected — signed in as alice · token until 1h");
  });

  test("loginReceipt: a non-rejection failure names r4's own first line", () => {
    const receipt = loginReceipt({ ok: false, raw: "✗ Gateway unreachable: connection refused\n  hint: …" });
    expect(receipt.text).toContain("Gateway unreachable: connection refused");
    expect(receipt.text).not.toContain("try /health");
  });
});
