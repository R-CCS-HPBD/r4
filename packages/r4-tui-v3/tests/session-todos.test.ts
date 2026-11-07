/**
 * Session todos lib — parse/normalize/count. Pins: full-rewrite semantics
 * (latest write wins), status vocabulary, no inference from empty/bad input.
 */

import { describe, expect, test } from "bun:test";
import {
  isTodoToolName,
  parseTodoPayload,
  todoCounts,
  todoSummaryLine,
} from "../src/lib/session-todos";

const SAMPLE = {
  todos: [
    { content: "Read current surfaces", status: "completed", priority: "high" },
    { content: "Wire TUI adapter", status: "in_progress", priority: "high" },
    { content: "Gates + docs", status: "pending", priority: "medium" },
    { content: "Abandoned idea", status: "cancelled", priority: "low" },
  ],
};

describe("parseTodoPayload", () => {
  test("parses {todos:[...]} object payloads", () => {
    const todos = parseTodoPayload(SAMPLE)!;
    expect(todos).toHaveLength(4);
    expect(todos[0]?.content).toBe("Read current surfaces");
    expect(todos[0]?.priority).toBe("high");
  });

  test("parses JSON-string payloads (bridge result bodies)", () => {
    const todos = parseTodoPayload(JSON.stringify(SAMPLE));
    expect(todos).toHaveLength(4);
  });

  test("parses bare arrays and title/task aliases", () => {
    const todos = parseTodoPayload([{ title: "t1", status: "done" }, { task: "t2" }]);
    expect(todos).toHaveLength(2);
    expect(todos![0]?.content).toBe("t1");
    expect(todos![0]?.status).toBe("done");
  });

  test("content-less rows are dropped; all-dropped = null", () => {
    expect(parseTodoPayload({ todos: [{ status: "pending" }] })).toBeNull();
  });

  test("garbage stays null — never an inferred todo", () => {
    expect(parseTodoPayload(undefined)).toBeNull();
    expect(parseTodoPayload("not json")).toBeNull();
    expect(parseTodoPayload({})).toBeNull();
    expect(parseTodoPayload({ todos: "nope" })).toBeNull();
  });

  test("status normalization lowercases and un-dashes", () => {
    const todos = parseTodoPayload([{ content: "x", status: "In Progress" }]);
    expect(todos?.[0]?.status).toBe("in_progress");
  });
});

describe("todo tool names", () => {
  test("matches todowrite variants, not other todo-named reads", () => {
    expect(isTodoToolName("todowrite")).toBe(true);
    expect(isTodoToolName("TodoWrite")).toBe(true);
    expect(isTodoToolName("todo_write")).toBe(true);
    expect(isTodoToolName("read")).toBe(false);
    expect(isTodoToolName("todolist_read")).toBe(false);
  });
});

describe("counts and summary", () => {
  test("counts classify by glyph vocabulary", () => {
    const counts = todoCounts(parseTodoPayload(SAMPLE)!);
    expect(counts.total).toBe(4);
    expect(counts.done).toBe(1);
    expect(counts.running).toBe(1);
    expect(counts.pending).toBe(1);
    expect(counts.cancelled).toBe(1);
  });

  test("summary line carries progress + running task + cancelled count", () => {
    const line = todoSummaryLine(parseTodoPayload(SAMPLE)!);
    expect(line).toContain("1/4 done");
    expect(line).toContain("▶ Wire TUI adapter");
    expect(line).toContain("✗ 1");
  });

  test("empty list has null summary (no fabricated progress)", () => {
    expect(todoSummaryLine([])).toBeNull();
  });
});
