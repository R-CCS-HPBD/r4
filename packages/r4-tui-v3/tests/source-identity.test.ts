/**
 * Round-3 task 7: client-side source digest + mismatch semantics.
 */
import { describe, expect, it } from "bun:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { computeSourceDigest, sourceTreeChanged } from "../src/lib/source-identity";

function makeTree(): string {
  const dir = mkdtempSync(join(tmpdir(), "r4-src-"));
  mkdirSync(join(dir, "pkg"));
  writeFileSync(join(dir, "pkg", "a.py"), "print(1)\n");
  writeFileSync(join(dir, "pkg", "b.py"), "print(2)\n");
  return dir;
}

describe("source identity digest", () => {
  it("is deterministic and changes with content", () => {
    const dir = makeTree();
    const d1 = computeSourceDigest(dir);
    expect(computeSourceDigest(dir)).toBe(d1);
    writeFileSync(join(dir, "pkg", "a.py"), "print(99)\n");
    expect(computeSourceDigest(dir)).not.toBe(d1);
    rmSync(dir, { recursive: true, force: true });
  });

  it("sourceTreeChanged degrades safely on unreadable dirs", () => {
    expect(
      sourceTreeChanged({ digest: "x", source_dir: "/nonexistent/r4-src-xyz" }),
    ).toBe(false);
  });

  it("sourceTreeChanged mirrors digest comparison", () => {
    const dir = makeTree();
    const identity = { digest: computeSourceDigest(dir), source_dir: dir };
    expect(sourceTreeChanged(identity)).toBe(false);
    identity.digest = "deadbeef";
    expect(sourceTreeChanged(identity)).toBe(true);
    rmSync(dir, { recursive: true, force: true });
  });
});
