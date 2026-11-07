// Console ↔ VS Code parity matrix (ADR-103 D-4).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  PARITY_MD_PATH,
  consoleRows,
  loadMatrix,
  renderMarkdown,
  syncMatrix,
  validateMatrix,
} from "../scripts/parity-matrix.mjs";

test("every console slash verb and pane has a matrix row, and no row is stale", () => {
  const { added, removed } = syncMatrix(loadMatrix(), consoleRows());
  assert.deepEqual(added, [], "unclassified console rows: run `node scripts/parity-matrix.mjs --write` and classify them");
  assert.deepEqual(removed, [], "rows the console no longer has: run `node scripts/parity-matrix.mjs --write`");
});

test("matrix rows are valid and the missing ceiling holds", () => {
  assert.deepEqual(validateMatrix(loadMatrix()), []);
});

test("PARITY.md is generated from the current matrix", () => {
  assert.equal(readFileSync(PARITY_MD_PATH, "utf8"), renderMarkdown(loadMatrix()));
});

test("validator rejects a raised missing count, absent evidence and unexplained statuses", () => {
  const matrix = {
    missing_max: 0,
    rows: [
      { id: "slash:/a", kind: "slash", status: "missing", wave: "VP-2" },
      { id: "slash:/b", kind: "slash", status: "done" },
      { id: "slash:/c", kind: "slash", status: "native" },
      { id: "slash:/d", kind: "slash", status: "partial", evidence: "no/such/file.ts", note: "x" },
      { id: "slash:/e", kind: "slash", status: "missing" },
      { id: "slash:/e", kind: "slash", status: "bogus" },
    ],
  };
  const problems = validateMatrix(matrix).join("\n");
  assert.match(problems, /missing rows 2 > missing_max 0/);
  assert.match(problems, /slash:\/b: done row needs evidence/);
  assert.match(problems, /slash:\/c: native row needs a note/);
  assert.match(problems, /slash:\/d: evidence no\/such\/file.ts does not exist/);
  assert.match(problems, /slash:\/e: missing row needs a wave/);
  assert.match(problems, /slash:\/e: duplicate row/);
  assert.match(problems, /unknown status "bogus"/);
});

test("sync adds a new console verb as missing/triage and drops a removed one", () => {
  const generated = new Map([
    ["slash:/kept", { kind: "slash", source: "console" }],
    ["slash:/new", { kind: "slash", source: "bridge" }],
  ]);
  const matrix = {
    missing_max: 1,
    rows: [
      { id: "slash:/kept", kind: "slash", source: "console", status: "missing", wave: "VP-2" },
      { id: "slash:/gone", kind: "slash", source: "console", status: "missing", wave: "VP-2" },
      { id: "interaction:hand", kind: "interaction", source: "console", status: "missing", wave: "VP-2" },
    ],
  };
  const { matrix: out, added, removed } = syncMatrix(matrix, generated);
  assert.deepEqual(added, ["slash:/new"]);
  assert.deepEqual(removed, ["slash:/gone"]);
  assert.deepEqual(
    out.rows.map((r) => [r.id, r.status, r.wave]),
    [
      ["slash:/kept", "missing", "VP-2"],
      ["slash:/new", "missing", "triage"],
      ["interaction:hand", "missing", "VP-2"],
    ],
  );
});
