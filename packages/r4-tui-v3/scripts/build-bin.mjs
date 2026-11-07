#!/usr/bin/env bun
/**
 * Compile the v3 TUI into self-contained single-file binaries.
 *
 * Usage:
 *   bun run scripts/build-bin.mjs                 # host platform only
 *   bun run scripts/build-bin.mjs --all           # full release matrix
 *   bun run scripts/build-bin.mjs --target=bun-linux-x64
 *
 * Output: dist-bin/r4-tui-<platform>[-<arch>]  (+ .sha256 files)
 */

import solidTransformPlugin from "@opentui/solid/bun-plugin";
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join } from "node:path";

const PACKAGE_DIR = new URL("..", import.meta.url).pathname;
const OUT_DIR = join(PACKAGE_DIR, "dist-bin");
const ENTRY = join(PACKAGE_DIR, "src", "cli.tsx");

const MATRIX = {
  "darwin-arm64": "bun-darwin-arm64",
  "linux-x64": "bun-linux-x64",
  "linux-x64-baseline": "bun-linux-x64-baseline",
  "linux-arm64": "bun-linux-arm64",
};

function hostPlatform() {
  const { platform, arch } = process;
  const key = `${platform}-${arch === "arm64" ? "arm64" : arch}`;
  if (!(key in MATRIX)) {
    console.error(`Unsupported host: ${key}. Supported: ${Object.keys(MATRIX).join(", ")}`);
    process.exit(1);
  }
  return key;
}

function parseTargets(argv) {
  const all = argv.includes("--all");
  const explicit = argv
    .filter((a) => a.startsWith("--target="))
    .map((a) => a.slice("--target=".length));
  if (all) return Object.keys(MATRIX);
  if (explicit.length) {
    for (const t of explicit) {
      if (!(t in MATRIX)) {
        console.error(`Unknown target: ${t}. Supported: ${Object.keys(MATRIX).join(", ")}`);
        process.exit(1);
      }
    }
    return explicit;
  }
  return [hostPlatform()];
}

function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const targets = parseTargets(process.argv.slice(2));
  for (const name of targets) {
    const outfile = join(OUT_DIR, `r4-tui-${name}`);
    console.log(`→ Bun.build compile ${MATRIX[name]}`);
    // Solid JSX requires the transform plugin — the runtime preload
    // (@opentui/solid/preload in bunfig.toml) is not applied to builds.
    const result = await Bun.build({
      entrypoints: [ENTRY],
      compile: { target: MATRIX[name], outfile },
      plugins: [solidTransformPlugin],
      minify: true,
      sourcemap: "none",
    });
    if (!result.success) {
      for (const log of result.logs) console.error(log);
      console.error(`✗ build failed for ${name}`);
      process.exit(1);
    }
    const digest = sha256File(outfile);
    writeFileSync(`${outfile}.sha256`, `${digest}  ${basename(outfile)}\n`);
    console.log(`✓ ${outfile}  sha256=${digest.slice(0, 12)}…`);
  }
}

await main();
