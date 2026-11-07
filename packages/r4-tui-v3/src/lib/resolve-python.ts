/**
 * Resolve a Python interpreter that can import `r4` for CLI + Companion bridge.
 *
 * `r4 console` sets R4_TUI_PYTHON=sys.executable. Without that, we still try:
 * PATH `r4` wrapper shebang/exec, workspace/.venv, parent/.venv, then PATH python.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/**
 * Dev monorepo checkout root — used ONLY to find the dev `.venv` during
 * Python resolution. Never use as the agent working directory: the bridge
 * and every cwd-anchored companion tool must run in the user's workspace
 * instead (see resolveWorkspace).
 */
export const DEV_CHECKOUT_ROOT = path.resolve(__dirname, "../../../..");

/**
 * The directory the console was launched for: the terminal cwd where the
 * user started the TUI, or `R4_TUI_WORKSPACE`. A new tab's bridge starts
 * here (`--fs-root`).
 */
export function launchWorkspace(): string {
  const explicit = process.env["R4_TUI_WORKSPACE"]?.trim();
  return explicit && explicit.length > 0 ? path.resolve(explicit) : process.cwd();
}

let workspaceOverride: (() => string) | null = null;

/** ADR-108 D-2: the stores name the active tab's worktree root here. */
export function setWorkspaceOverride(read: (() => string) | null): void {
  workspaceOverride = read;
}

/**
 * Directory the agent treats as its workspace: the active tab's worktree
 * while it works in one (`/worktree`), else {@link launchWorkspace}.
 */
export function resolveWorkspace(): string {
  const override = workspaceOverride?.()?.trim();
  return override ? override : launchWorkspace();
}

let cachedPython: string | null | undefined;
let cachedPathR4: string | null | undefined;

/** Test helper — clear memoized resolution. */
export function resetResolvedPythonCache(): void {
  cachedPython = undefined;
  cachedPathR4 = undefined;
}

export function findPathR4(): string | null {
  if (cachedPathR4 !== undefined) return cachedPathR4;
  try {
    const cmd = process.platform === "win32" ? ["where.exe", "r4"] : ["which", "r4"];
    const proc = Bun.spawnSync(cmd, { stdout: "pipe", stderr: "pipe" });
    if (proc.exitCode !== 0) {
      cachedPathR4 = null;
      return null;
    }
    const first = new TextDecoder()
      .decode(proc.stdout)
      .trim()
      .split(/\r?\n/)
      .map((l) => l.trim())
      .find((l) => l.length > 0);
    cachedPathR4 = first && fs.existsSync(first) ? first : null;
    return cachedPathR4;
  } catch {
    cachedPathR4 = null;
    return null;
  }
}

/** Extract python path from a pip/uv `r4` launcher script (bash or shebang). */
export function pythonFromR4Wrapper(r4Path: string): string | null {
  try {
    const text = fs.readFileSync(r4Path, "utf8");
    const execDq = text.match(/\bexec\s+"([^"\n]+)"/);
    if (execDq?.[1] && looksLikePython(execDq[1]) && fs.existsSync(execDq[1])) {
      return execDq[1];
    }
    const execSq = text.match(/\bexec\s+'([^'\n]+)'/);
    if (execSq?.[1] && looksLikePython(execSq[1]) && fs.existsSync(execSq[1])) {
      return execSq[1];
    }
    const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
    if (firstLine.startsWith("#!")) {
      const bin = firstLine.slice(2).trim().split(/\s+/)[0];
      if (bin && looksLikePython(bin) && fs.existsSync(bin)) return bin;
    }
  } catch {
    /* ignore */
  }
  return null;
}

function looksLikePython(p: string): boolean {
  const base = path.basename(p).toLowerCase();
  return base === "python" || base === "python3" || base.startsWith("python");
}

function venvCandidates(root: string): string[] {
  if (process.platform === "win32") {
    return [path.join(root, ".venv", "Scripts", "python.exe")];
  }
  return [path.join(root, ".venv", "bin", "python"), path.join(root, ".venv", "bin", "python3")];
}

function canImportR4(python: string): boolean {
  try {
    const proc = Bun.spawnSync([python, "-c", "import r4"], {
      stdout: "pipe",
      stderr: "pipe",
      timeout: 8_000,
    });
    return proc.exitCode === 0;
  } catch {
    return false;
  }
}

function candidateList(): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (p: string | null | undefined) => {
    if (!p || seen.has(p)) return;
    seen.add(p);
    out.push(p);
  };

  const pathR4 = findPathR4();
  if (pathR4) push(pythonFromR4Wrapper(pathR4));

  for (const root of [DEV_CHECKOUT_ROOT, path.dirname(DEV_CHECKOUT_ROOT)]) {
    for (const v of venvCandidates(root)) {
      if (fs.existsSync(v)) push(v);
    }
  }

  push(process.platform === "win32" ? "python" : "python3");
  if (process.platform === "win32") push("python3");
  else push("python");

  return out;
}

/**
 * Python that can `import r4`. Memoized.
 * `R4_TUI_PYTHON` always wins (set by `r4 console` to sys.executable).
 * Otherwise probe PATH `r4` wrapper, workspace/.venv, parent/.venv, then PATH python.
 */
export function resolvePython(): string {
  if (cachedPython !== undefined && cachedPython !== null) return cachedPython;

  const explicit = process.env["R4_TUI_PYTHON"]?.trim();
  if (explicit) {
    cachedPython = explicit;
    return explicit;
  }

  const candidates = candidateList();
  for (const cand of candidates) {
    const absoluteOk = cand.includes(path.sep) || cand.includes("/") ? fs.existsSync(cand) : true;
    if (!absoluteOk) continue;
    if (canImportR4(cand)) {
      cachedPython = cand;
      return cand;
    }
  }

  // Prefer wrapper/venv even when probe fails (clearer spawn errors).
  const fallback = candidates[0] ?? (process.platform === "win32" ? "python" : "python3");
  cachedPython = fallback;
  return fallback;
}

/** Alias used by the Companion bridge. */
export function resolveBridgePython(): string {
  return resolvePython();
}

/**
 * r4 CLI for in-TUI commands (skills, sources, /connect, version probe).
 * `r4 console` pins R4_TUI_PYTHON to its own interpreter: use it, so the
 * commands hit the same install as the bridge, never another clone's `r4`
 * that happens to be first on PATH. Without the pin, prefer the PATH `r4`.
 */
export function resolveR4Command(): string[] {
  const pinned = process.env["R4_TUI_PYTHON"]?.trim();
  const pathR4 = pinned ? null : findPathR4();
  if (pathR4) return [pathR4];
  const python = resolvePython();
  return [
    python,
    "-c",
    "import sys; from r4.cli import main; sys.argv = ['r4'] + sys.argv[1:]; raise SystemExit(main() or 0)",
  ];
}
