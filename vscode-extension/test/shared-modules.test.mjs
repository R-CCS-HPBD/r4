// Console modules the extension imports stay framework-free (ADR-103 D-3):
// no Solid, OpenTUI or Bun anywhere in their import graph, and no `Bun.` call.
import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const rootDir = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const consoleSrc = path.resolve(rootDir, "..", "packages", "r4-tui-v3", "src");
const IMPORT_RE = /(?:import|export)\s[^"'`]*?from\s+["']([^"']+)["']|import\(\s*["']([^"']+)["']\s*\)|require\(\s*["']([^"']+)["']\s*\)/g;
const FORBIDDEN = /^(solid-js|@opentui\/|bun(:|$))/;

function specifiers(file) {
  const text = readFileSync(file, "utf8");
  return [...text.matchAll(IMPORT_RE)].map((m) => m[1] ?? m[2] ?? m[3]);
}

/** Source without block and line comments (prose may say "Bun."). */
function withoutComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
}

function tsFiles(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    if (statSync(full).isDirectory()) return tsFiles(full);
    return /\.tsx?$/.test(full) ? [full] : [];
  });
}

function resolveTs(from, spec) {
  const base = path.resolve(path.dirname(from), spec);
  for (const candidate of [base, `${base}.ts`, `${base}.tsx`, path.join(base, "index.ts")]) {
    try {
      if (statSync(candidate).isFile()) return candidate;
    } catch {
      /* next */
    }
  }
  return null;
}

/** Console files the extension reaches, directly or through their imports. */
function sharedGraph() {
  const queue = [];
  // The extension host and the webview both import console modules.
  for (const file of [...tsFiles(path.join(rootDir, "src")), ...tsFiles(path.join(rootDir, "webview-ui", "src"))]) {
    for (const spec of specifiers(file)) {
      const target = spec.startsWith(".") ? resolveTs(file, spec) : null;
      if (target?.startsWith(consoleSrc)) queue.push(target);
    }
  }
  const seen = new Set();
  const problems = [];
  while (queue.length) {
    const file = queue.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    const rel = path.relative(consoleSrc, file);
    if (/\bBun\.\w/.test(withoutComments(readFileSync(file, "utf8")))) problems.push(`${rel} calls Bun.*`);
    for (const spec of specifiers(file)) {
      if (FORBIDDEN.test(spec)) problems.push(`${rel} imports ${spec}`);
      if (!spec.startsWith(".")) continue;
      const target = resolveTs(file, spec);
      if (!target) problems.push(`${rel}: cannot resolve ${spec}`);
      else queue.push(target);
    }
  }
  return { files: [...seen].map((f) => path.relative(consoleSrc, f)).sort(), problems };
}

test("the extension imports console modules, and they are framework-free", () => {
  const { files, problems } = sharedGraph();
  assert.ok(files.includes(path.join("bridge", "wire.ts")), `shared: ${files.join(", ")}`);
  assert.ok(files.includes(path.join("lib", "paste-tracker.ts")), "the webview's imports are walked too");
  assert.deepEqual(problems, []);
});
