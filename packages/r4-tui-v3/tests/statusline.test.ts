import { afterEach, beforeEach, expect, test } from "bun:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  STATUSLINE_MAX_CHARS,
  readStatuslineCommand,
  runStatusline,
  statuslinePath,
  writeStatuslineCommand,
} from "../src/lib/statusline";

let dir = "";
const saved = process.env.R4_CONFIG_HOME;
const ctx = { model: "qwen", project: "proj-1", session: "s1", permission: "ask_always" };

beforeEach(() => {
  dir = mkdtempSync(join(tmpdir(), "r4-statusline-"));
  process.env.R4_CONFIG_HOME = dir;
});

afterEach(() => {
  rmSync(dir, { recursive: true, force: true });
  if (saved === undefined) delete process.env.R4_CONFIG_HOME;
  else process.env.R4_CONFIG_HOME = saved;
});

test("set, read and remove the status line command", () => {
  expect(readStatuslineCommand()).toBe("");
  writeStatuslineCommand("echo hi");
  expect(statuslinePath()).toBe(join(dir, "statusline"));
  expect(readStatuslineCommand()).toBe("echo hi");
  writeStatuslineCommand("off");
  expect(readStatuslineCommand()).toBe("");
});

test("runs with R4_* context and keeps the first line", () => {
  expect(runStatusline('printf "%s/%s\\nsecond" "$R4_PROJECT" "$R4_MODEL"', ctx)).toBe("proj-1/qwen");
});

test("failures and empty commands render nothing; long lines are clipped", () => {
  expect(runStatusline("", ctx)).toBe("");
  expect(runStatusline("exit 3", ctx)).toBe("");
  const long = runStatusline(`printf '%0.sx' $(seq 1 200)`, ctx);
  expect(long.length).toBe(STATUSLINE_MAX_CHARS);
});
