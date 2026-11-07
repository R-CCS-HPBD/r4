/**
 * Python / r4 launcher resolution for CLI + Companion bridge.
 */

import { describe, it, expect, afterEach, beforeEach } from "bun:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  findPathR4,
  pythonFromR4Wrapper,
  resetResolvedPythonCache,
  resolvePython,
  resolveR4Command,
} from "../src/lib/resolve-python";

const envBackup = { ...process.env };

beforeEach(() => {
  resetResolvedPythonCache();
});

afterEach(() => {
  process.env = { ...envBackup };
  resetResolvedPythonCache();
});

describe("pythonFromR4Wrapper", () => {
  it("parses exec \"…/python\" wrappers", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "r4-wrap-"));
    const fakePy = path.join(dir, "python");
    fs.writeFileSync(fakePy, "#!/bin/sh\n");
    fs.chmodSync(fakePy, 0o755);
    const wrap = path.join(dir, "r4");
    fs.writeFileSync(
      wrap,
      `#!/usr/bin/env bash\nexec "${fakePy}" -m r4.cli "$@"\n`,
    );
    expect(pythonFromR4Wrapper(wrap)).toBe(fakePy);
  });

  it("parses python shebang", () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "r4-shebang-"));
    const fakePy = path.join(dir, "python3");
    fs.writeFileSync(fakePy, "#!/bin/sh\n");
    fs.chmodSync(fakePy, 0o755);
    const wrap = path.join(dir, "r4");
    fs.writeFileSync(wrap, `#!${fakePy}\n`);
    expect(pythonFromR4Wrapper(wrap)).toBe(fakePy);
  });
});

describe("resolvePython", () => {
  it("honors R4_TUI_PYTHON unconditionally", () => {
    process.env.R4_TUI_PYTHON = "/explicit/python-from-r4-console";
    expect(resolvePython()).toBe("/explicit/python-from-r4-console");
  });
});

describe("resolveR4Command", () => {
  it("uses the pinned R4_TUI_PYTHON, never the first r4 on PATH", () => {
    process.env.R4_TUI_PYTHON = "/clone-a/.venv/bin/python";
    const cmd = resolveR4Command();
    expect(cmd[0]).toBe("/clone-a/.venv/bin/python");
    expect(cmd[1]).toBe("-c");
  });

  it("keeps the PATH r4 first without the pin", () => {
    delete process.env.R4_TUI_PYTHON;
    const pathR4 = findPathR4();
    if (pathR4) expect(resolveR4Command()).toEqual([pathR4]);
    else expect(resolveR4Command()[1]).toBe("-c");
  });
});
