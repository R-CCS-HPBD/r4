/**
 * Shared tool-argument helpers (ported from tui-js tool-activity.mjs).
 */

export type ToolArgs = Record<string, unknown>;

export function parseToolArgs(raw: unknown): ToolArgs {
  if (!raw) return {};
  try {
    return (typeof raw === "string" ? JSON.parse(raw) : raw) as ToolArgs;
  } catch {
    return {};
  }
}

export function basename(path: unknown): string {
  const normalized = String(path || "").replace(/\\/g, "/").replace(/\/+$/, "");
  const parts = normalized.split("/").filter(Boolean);
  return parts[parts.length - 1] || normalized || "";
}

/** One-line summary of common arg shapes for activity rows. */
export function argSummary(args: ToolArgs): string {
  if (!args || typeof args !== "object") return "";
  const a = args as Record<string, unknown>;
  if (a.path && a.pattern) return `"${a.pattern}" in ${a.path}`;
  if (a.path) return String(a.path);
  if (a.pattern) return String(a.pattern);
  if (a.query) return `"${String(a.query).slice(0, 40)}"`;
  if (a.command) return String(a.command).slice(0, 50);
  const first = Object.values(a).find(
    (v): v is string => typeof v === "string" && v.length > 0,
  );
  return first ? first.slice(0, 50) : "";
}

/** Short pattern hint from search-style args. */
export function patternHint(args: ToolArgs): string {
  const pattern = (args as Record<string, unknown>).pattern ??
    (args as Record<string, unknown>).glob_pattern ??
    (args as Record<string, unknown>).glob;
  if (!pattern) return "";
  return String(pattern).slice(0, 48);
}

/** Label for a path arg: "project root" for cwd-ish, basename otherwise. */
export function formatPathLabel(rawPath: unknown): string {
  const path = String(rawPath || "").trim().replace(/\\/g, "/");
  if (!path || path === "." || path === "./") return "project root";
  return basename(path) || path;
}
