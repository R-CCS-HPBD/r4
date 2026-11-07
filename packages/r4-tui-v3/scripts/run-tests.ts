/**
 * Run the bun test suite in batches of files, one bun process per batch.
 *
 * Why: `bun test --parallel=1` evaluates every test file's module graph anew
 * and keeps the old ones (about 350 modules per file here), so one process
 * running all 255+ files grew to ≈6 GB of JS heap and the CI runner's renderer
 * allocations failed with OutOfMemory (2026-10-17). A fresh process per batch
 * bounds memory by the batch, whatever the suite size.
 *
 * Usage:
 *   bun scripts/run-tests.ts                 # whole suite, batched
 *   bun scripts/run-tests.ts tests/a.test.ts # named files: one process, as `bun test`
 *   R4_TEST_BATCH_SIZE=30 bun scripts/run-tests.ts
 *   R4_TEST_CONCURRENCY=2 R4_TEST_BATCH_SIZE=20 bun scripts/run-tests.ts  # CI (2026-11-03)
 */

import { readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = join(import.meta.dir, "..");
const BUN_ARGS = ["test", "--parallel=1", "--timeout", "30000"];
const DEFAULT_BATCH = 40;

function testFiles(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir).sort()) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...testFiles(path));
    else if (/\.test\.tsx?$/.test(name)) out.push(relative(ROOT, path));
  }
  return out;
}

function batches(files: string[], size: number): string[][] {
  const out: string[][] = [];
  for (let i = 0; i < files.length; i += size) out.push(files.slice(i, i + size));
  return out;
}

function batchSize(): number {
  const raw = Number.parseInt(process.env.R4_TEST_BATCH_SIZE ?? "", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_BATCH;
}

/** pass / fail / skip counts from bun's summary lines. */
function counts(text: string): { pass: number; fail: number; skip: number } {
  const read = (word: string) => Number(new RegExp(`^\\s*(\\d+) ${word}$`, "m").exec(text)?.[1] ?? 0);
  return { pass: read("pass"), fail: read("fail"), skip: read("skip") };
}

/** Batches run at once (R4_TEST_CONCURRENCY, default 1). Each bun process
 * holds its whole batch's module graphs (≈2.4 GB for 40 files), so CI pairs
 * 2 lanes with batches of 20 to keep the peak where one 40-file batch was. */
function concurrency(): number {
  const raw = Number.parseInt(process.env.R4_TEST_CONCURRENCY ?? "", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : 1;
}

type BatchResult = { code: number; text: string; out: string };

async function drain(stream: ReadableStream<Uint8Array>, echo: boolean): Promise<string> {
  const chunks: string[] = [];
  const decoder = new TextDecoder();
  for await (const chunk of stream) {
    const text = decoder.decode(chunk);
    chunks.push(text);
    if (echo) process.stderr.write(text);
  }
  return chunks.join("");
}

/** live: stream the batch as it runs (one lane); else buffer it for one report. */
async function runBatch(files: string[], live = true): Promise<BatchResult> {
  const proc = Bun.spawn(["bun", ...BUN_ARGS, ...files], {
    cwd: ROOT,
    stdout: live ? "inherit" : "pipe",
    stderr: "pipe",
  });
  // bun prints results and the summary on stderr: keep a copy for the counts.
  const stdout = live ? Promise.resolve("") : drain(proc.stdout as ReadableStream<Uint8Array>, false);
  const [text, out] = await Promise.all([drain(proc.stderr, live), stdout]);
  return { code: await proc.exited, text, out };
}

/** Run every batch, `width` at a time; results keep the batch order. */
async function runAll(groups: string[][], width: number): Promise<BatchResult[]> {
  const live = width === 1;
  const results: BatchResult[] = new Array(groups.length);
  let next = 0;
  const lane = async () => {
    while (next < groups.length) {
      const index = next++;
      const header = `\n── batch ${index + 1}/${groups.length} (${groups[index].length} files) ──\n`;
      if (live) process.stderr.write(header);
      const result = await runBatch(groups[index], live);
      if (!live) {
        process.stdout.write(result.out);
        process.stderr.write(header + result.text);
      }
      results[index] = result;
    }
  };
  await Promise.all(Array.from({ length: Math.min(width, groups.length) }, lane));
  return results;
}

async function main(): Promise<number> {
  const named = process.argv.slice(2);
  if (named.length > 0) {
    return (await runBatch(named)).code;
  }
  const groups = batches(testFiles(join(ROOT, "tests")), batchSize());
  const total = { pass: 0, fail: 0, skip: 0 };
  let failedBatches = 0;
  for (const { code, text } of await runAll(groups, concurrency())) {
    const c = counts(text);
    total.pass += c.pass;
    total.fail += c.fail;
    total.skip += c.skip;
    if (code !== 0) failedBatches += 1;
  }
  process.stderr.write(
    `\n══ ${groups.length} batches: ${total.pass} pass, ${total.skip} skip, ${total.fail} fail` +
      `${failedBatches ? ` (${failedBatches} batch(es) exited non-zero)` : ""} ══\n`,
  );
  return failedBatches > 0 ? 1 : 0;
}

process.exit(await main());
