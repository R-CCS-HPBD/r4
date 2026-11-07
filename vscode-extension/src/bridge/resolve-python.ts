/**
 * The Python interpreter that runs the companion bridge (ADR-103, VP-1).
 *
 * Same order as the console (`packages/r4-tui-v3/src/lib/resolve-python.ts`),
 * with the extension's setting first: `r4.pythonPath` → `R4_TUI_PYTHON` →
 * the interpreter the `r4` launcher runs → `<workspace>/.venv` → `python3`
 * / `python`. The first candidate that can `import r4` wins.
 */

import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export interface PythonResolveInput {
  /** `r4.pythonPath` ("" when unset). */
  setting: string;
  env: Record<string, string | undefined>;
  /** Path of the `r4` launcher the extension resolved (`r4.cliPath` logic). */
  cliPath: string;
  workspace: string;
  platform?: NodeJS.Platform;
}

export interface PythonResolveDeps {
  readFile: (file: string) => string;
  exists: (file: string) => boolean;
  canImportR4: (python: string) => boolean;
}

const defaultDeps: PythonResolveDeps = {
  readFile: (file) => fs.readFileSync(file, "utf8"),
  exists: (file) => fs.existsSync(file),
  canImportR4: (python) => {
    try {
      return spawnSync(python, ["-c", "import r4"], { stdio: "ignore", timeout: 8_000 }).status === 0;
    } catch {
      return false;
    }
  },
};

function looksLikePython(file: string): boolean {
  return path.basename(file).toLowerCase().startsWith("python");
}

/** The interpreter a pip/uv `r4` launcher runs (`exec "…"` or its shebang). */
export function pythonFromLauncher(text: string): string | null {
  const exec = text.match(/\bexec\s+["']([^"'\n]+)["']/);
  if (exec?.[1] && looksLikePython(exec[1])) return exec[1];
  const firstLine = text.split(/\r?\n/, 1)[0] ?? "";
  if (!firstLine.startsWith("#!")) return null;
  const bin = firstLine.slice(2).trim().split(/\s+/)[0];
  return bin && looksLikePython(bin) ? bin : null;
}

function launcherPython(cliPath: string, deps: PythonResolveDeps): string | null {
  if (!path.isAbsolute(cliPath) || !deps.exists(cliPath)) return null;
  try {
    return pythonFromLauncher(deps.readFile(cliPath));
  } catch {
    return null;
  }
}

function venvPythons(workspace: string, platform: NodeJS.Platform): string[] {
  if (platform === "win32") return [path.join(workspace, ".venv", "Scripts", "python.exe")];
  return [path.join(workspace, ".venv", "bin", "python"), path.join(workspace, ".venv", "bin", "python3")];
}

/** Candidates in order, deduplicated, before the import check. */
export function pythonCandidates(input: PythonResolveInput, deps: PythonResolveDeps = defaultDeps): string[] {
  const platform = input.platform ?? process.platform;
  const listed = [
    input.setting.trim(),
    (input.env["R4_TUI_PYTHON"] ?? "").trim(),
    launcherPython(input.cliPath, deps),
    ...venvPythons(input.workspace, platform).filter((p) => deps.exists(p)),
    platform === "win32" ? "python" : "python3",
    "python",
  ];
  return [...new Set(listed.filter((p): p is string => Boolean(p)))];
}

/** The first candidate that imports r4, or null with the list it tried. */
export function resolveBridgePython(
  input: PythonResolveInput,
  deps: PythonResolveDeps = defaultDeps,
): { python: string | null; tried: string[] } {
  const tried = pythonCandidates(input, deps);
  return { python: tried.find((p) => deps.canImportR4(p)) ?? null, tried };
}
