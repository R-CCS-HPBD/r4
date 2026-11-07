/**
 * A bash stand-in for the r4 interpreter (S4 tests). resolveR4Command() with
 * R4_TUI_PYTHON pinned spawns [python, "-c", <code>, ...r4 args]; the fake
 * drops `-c <code>`, records its argv and stdin, then behaves per its mode.
 * Paths and mode are baked into the script: a spawned child does not see
 * process.env edits made at test time.
 */
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { resetResolvedPythonCache } from "../../src/lib/resolve-python";

export const PROVIDER_KEY = "sk-live-S4-provider-key-0123456789";

export type FakeMode = "ok" | "echo" | "sleep" | "old" | "sources" | "authstatus";

/** What `sources` mode prints for `r4 source list --json`: one keyed platform source. */
export const FAKE_PLATFORM_SOURCES = [
  { name: "rccs", kind: "platform", url: "https://gw.example/rccs", active: true, has_api_key: true },
];

export interface FakeR4 {
  python: string;
  argv(): string[];
  stdin(): string;
  spawned(): boolean;
  /** Every spawn in order (argv() and stdin() keep last-call semantics) —
   *  flows that legitimately spawn r4 twice (login → auth status receipt,
   *  2026-10-21) assert per call. */
  calls(): Array<{ argv: string[]; stdin: string }>;
}

const BEHAVIOUR: Record<FakeMode, (dir: string) => string> = {
  ok: () => `echo "Added source (public)"`,
  // Echo argv AND stdin back on stderr — the worst case for leaking.
  echo: (dir) => `echo "Error: rejected argv=[$*] stdin=[$(cat "${dir}/stdin")]" >&2; exit 1`,
  sleep: () => "sleep 5",
  // An r4 that predates --api-key-stdin: argparse prints a long usage block
  // (past the console's 300-char cut), then rejects the flag (exit 2).
  old: () =>
    `echo "usage: r4 auth login [-h] ${"[--option-with-a-long-name VALUE] ".repeat(12)}" >&2; ` +
    `echo "r4 auth login: error: unrecognized arguments: --api-key-stdin" >&2; exit 2`,
  sources: () => `echo '${JSON.stringify(FAKE_PLATFORM_SOURCES)}'`,
  // What `r4 auth status` prints for a JWT profile (r4/_cli/cli.py's
  // _show_jwt_identity — the CLI has no --json; the receipt must parse text).
  authstatus: () =>
    `printf '%s\\n' ` +
    `"✓ Profile 'default' has credentials and Gateway accepts them" ` +
    `"  Gateway: https://gateway.example.jp" ` +
    `"  JWT identity (display only):" ` +
    `"    Username: mnagaso" ` +
    `"    Expires in: 719h 12m"`,
};

const createdDirs: string[] = [];

export function makeFakeR4(mode: FakeMode): FakeR4 {
  const dir = mkdtempSync(join(tmpdir(), "r4-fake-"));
  createdDirs.push(dir);
  const python = join(dir, "python");
  writeFileSync(
    python,
    [
      "#!/usr/bin/env bash",
      "shift 2 # drop: -c <code>",
      `n=$(cat "${dir}/seq" 2>/dev/null || echo 0); n=$((n + 1)); printf '%s' "$n" > "${dir}/seq"`,
      `printf '%s\\n' "$@" > "${dir}/calls-$n.argv"`,
      `cat > "${dir}/calls-$n.stdin"`,
      `cp "${dir}/calls-$n.argv" "${dir}/argv"`,
      `cp "${dir}/calls-$n.stdin" "${dir}/stdin"`,
      BEHAVIOUR[mode](dir),
      "",
    ].join("\n"),
  );
  chmodSync(python, 0o755);
  return {
    python,
    argv: () => readFileSync(join(dir, "argv"), "utf8").split("\n").filter(Boolean),
    stdin: () => readFileSync(join(dir, "stdin"), "utf8"),
    spawned: () => existsSync(join(dir, "argv")),
    calls: () => {
      const seq = existsSync(join(dir, "seq"))
        ? Number(readFileSync(join(dir, "seq"), "utf8").trim())
        : 0;
      const out: Array<{ argv: string[]; stdin: string }> = [];
      for (let i = 1; i <= seq; i += 1) {
        out.push({
          argv: readFileSync(join(dir, `calls-${i}.argv`), "utf8").split("\n").filter(Boolean),
          stdin: readFileSync(join(dir, `calls-${i}.stdin`), "utf8"),
        });
      }
      return out;
    },
  };
}

const savedPython = process.env.R4_TUI_PYTHON;

/** Point resolveR4Command() at `python` (a fake, or a missing path). */
export function useFakeR4(python: string): void {
  process.env.R4_TUI_PYTHON = python;
  resetResolvedPythonCache();
}

/** Restore R4_TUI_PYTHON as it was when this module loaded; drop the fakes. */
export function restoreR4(): void {
  if (savedPython === undefined) delete process.env.R4_TUI_PYTHON;
  else process.env.R4_TUI_PYTHON = savedPython;
  resetResolvedPythonCache();
  for (const dir of createdDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
}
