/**
 * Session todo list — mirrored struct of the agent's todowrite calls.
 *
 * The agent owns the todo list; the TUI mirrors the LATEST full write
 * (todowrite replaces the whole list by contract). Parse + presentation
 * helpers are pure; the store keeps exactly what the agent last sent —
 * nothing is inferred from chat text or from tool names.
 */

/** Tool names that carry full rewrites of the session todo list. */
export function isTodoToolName(name: string): boolean {
  return /todowrite|todo_write|^todo$/i.test(name);
}

export interface SessionTodo {
  content: string;
  status: string;
  priority: string;
}

export const TODO_GLYPH: Record<string, string> = {
  completed: "✓",
  finished: "✓",
  done: "✓",
  in_progress: "▶",
  running: "▶",
  pending: "□",
  todo: "□",
  cancelled: "✗",
  canceled: "✗",
};

/** Priority chip for rows that declare one (high/medium/low, else dimmed). */
export const TODO_PRIORITY_SHORT: Record<string, string> = {
  high: "H",
  medium: "M",
  low: "L",
};

function normalizeStatus(raw: unknown): string {
  return String(raw ?? "pending").trim().toLowerCase().replace(/\s+/g, "_");
}

function itemContent(item: Record<string, unknown>): string {
  return String(item["content"] ?? item["title"] ?? item["task"] ?? "").trim();
}

function normalizeItem(raw: unknown): SessionTodo | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const item = raw as Record<string, unknown>;
  const content = itemContent(item);
  if (!content) return null;
  return {
    content,
    status: normalizeStatus(item["status"]),
    priority: normalizeStatus(item["priority"] ?? ""),
  };
}

/** Candidate array behind the payload: itself, its .todos, or nothing. */
function payloadList(raw: unknown): unknown[] | null {
  if (Array.isArray(raw)) return raw;
  if (raw && typeof raw === "object") {
    const todos = (raw as Record<string, unknown>)["todos"];
    if (Array.isArray(todos)) return todos;
  }
  return null;
}

/**
 * Parse one todowrite arguments payload → full list or null.
 * Accepts `{todos: [...]}` objects, raw JSON strings, or a bare array.
 */
export function parseTodoPayload(raw: unknown): SessionTodo[] | null {
  let candidate = raw;
  if (typeof candidate === "string") {
    try {
      candidate = JSON.parse(candidate);
    } catch {
      return null;
    }
  }
  const list = payloadList(candidate);
  if (!list) return null;
  const items = list.map(normalizeItem).filter((i): i is SessionTodo => i !== null);
  return items.length > 0 ? items : null;
}

export interface TodoCounts {
  done: number;
  running: number;
  pending: number;
  cancelled: number;
  total: number;
}

export function todoCounts(todos: SessionTodo[]): TodoCounts {
  const counts: TodoCounts = { done: 0, running: 0, pending: 0, cancelled: 0, total: todos.length };
  for (const todo of todos) {
    const glyph = TODO_GLYPH[todo.status] ?? "□";
    if (glyph === "✓") counts.done += 1;
    else if (glyph === "▶") counts.running += 1;
    else if (glyph === "✗") counts.cancelled += 1;
    else counts.pending += 1;
  }
  return counts;
}

/** One-line progress summary ("3/6 done · ▶ current task") for chips/status bars. */
export function todoSummaryLine(todos: SessionTodo[]): string | null {
  if (todos.length === 0) return null;
  const counts = todoCounts(todos);
  const running = todos.find((t) => (TODO_GLYPH[t.status] ?? "□") === "▶");
  const parts = [`${counts.done}/${counts.total} done`];
  if (running) parts.push(`▶ ${running.content.slice(0, 40)}`);
  if (counts.cancelled > 0) parts.push(`✗ ${counts.cancelled}`);
  return parts.join(" · ");
}
